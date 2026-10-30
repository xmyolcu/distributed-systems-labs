import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .policies import BackpressurePolicy


class QueueFull(Exception):
    """Kuyruk dolu olduğu için görev kabul edilmedi (REJECT veya BLOCK timeout)."""


@dataclass(slots=True)
class _Task:
    fn: Callable[..., Any]
    args: tuple
    # Görev kuyruğa gerçekten girdiği an _StampedQueue tarafından yazılır (monotonic saat).
    enqueued_at: float = 0.0


@dataclass(frozen=True)
class PoolStats:
    submitted: int
    completed: int
    failed: int
    dropped: int
    rejected: int
    avg_wait_s: float
    p95_wait_s: float
    queue_length: int


# Worker'lara "çık" demek için kuyruğa konan işaret. Her worker için bir tane.
_SENTINEL = None


class _StampedQueue(queue.Queue):
    """enqueued_at'i görevin kuyruğa GERÇEKTEN girdiği anda damgalar.

    queue.Queue, _put'u kendi iç kilidi altında, yer açıldıktan sonra çağırır.
    Böylece BLOCK'ta üreticinin put() içinde beklediği süre "kuyruk bekleme"
    ölçümüne karışmaz (aksi halde submit gecikmesiyle çift sayılırdı).
    """

    def _put(self, item):
        if item is not None:  # sentinel'e damga vurma
            item.enqueued_at = time.monotonic()
        super()._put(item)


class WorkerPool:
    def __init__(
        self,
        num_workers: int,
        queue_size: int,
        policy: BackpressurePolicy = BackpressurePolicy.BLOCK,
    ) -> None:
        if num_workers < 1:
            raise ValueError("num_workers >= 1 olmalı")
        # maxsize=0 queue.Queue'da "sınırsız" demek; backpressure için sınır şart.
        if queue_size < 1:
            raise ValueError("queue_size >= 1 olmalı")

        self._policy = policy
        self._queue: queue.Queue[_Task | None] = _StampedQueue(maxsize=queue_size)

        # Sayaçlar ve kapanış durumu tek kilitle korunur; basit ve tutarlı snapshot verir.
        self._lock = threading.Lock()
        # shutdown(), o an submit() içinde olan üreticilerin bitmesini bu koşulla bekler.
        self._no_inflight = threading.Condition(self._lock)
        self._inflight = 0
        self._closed = False

        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._dropped = 0
        self._rejected = 0
        # Lab ölçeği için tüm örnekleri tutuyoruz; uzun ömürlü sistemde histogram/reservoir gerekir.
        self._wait_times: list[float] = []

        self._threads = [
            threading.Thread(target=self._worker_loop, name=f"worker-{i}", daemon=True)
            for i in range(num_workers)
        ]
        for t in self._threads:
            t.start()

    # ------------------------------------------------------------------ üretici

    def submit(self, fn: Callable[..., Any], *args: Any, timeout: float | None = None) -> bool:
        """Görevi kuyruğa koyar. Kabul edilirse True, DROP ile atılırsa False döner.

        timeout yalnızca BLOCK politikasında anlamlıdır.
        """
        with self._lock:
            # Kapanmış havuza görev kabul edilmez; sessizce yutmak yerine açıkça hata veriyoruz.
            if self._closed:
                raise RuntimeError("WorkerPool kapatıldı, yeni görev kabul edilmiyor")
            # shutdown() sentinel'leri koymadan önce bu sayının sıfırlanmasını bekler.
            # Böylece sentinel'lerden SONRA kuyruğa giren "yetim" görev olamaz.
            self._inflight += 1

        try:
            # Zaman damgası burada vurulmaz; kuyruğa girerken _StampedQueue vurur.
            task = _Task(fn, args)
            try:
                if self._policy is BackpressurePolicy.BLOCK:
                    # Kilit dışında bekliyoruz: aksi halde worker'lar ve shutdown() kilitlenirdi.
                    self._queue.put(task, block=True, timeout=timeout)
                else:
                    self._queue.put_nowait(task)
            except queue.Full:
                with self._lock:
                    if self._policy is BackpressurePolicy.DROP:
                        self._dropped += 1
                        return False
                    # REJECT ve BLOCK-timeout aynı anlamı taşır: üretici reddedildiğini bilir.
                    self._rejected += 1
                raise QueueFull("kuyruk dolu") from None

            with self._lock:
                self._submitted += 1
            return True
        finally:
            with self._lock:
                self._inflight -= 1
                if self._inflight == 0:
                    self._no_inflight.notify_all()

    # ------------------------------------------------------------------ worker

    def _worker_loop(self) -> None:
        while True:
            task = self._queue.get()
            if task is _SENTINEL:
                return

            # Bekleme süresi worker görevi aldığı anda ölçülür; çalışma süresi dahil değil.
            wait = time.monotonic() - task.enqueued_at
            try:
                task.fn(*task.args)
            except Exception:
                # Görev hatası worker'ı öldürmemeli; sayılır ve döngü devam eder.
                # BaseException (KeyboardInterrupt vb.) bilerek yakalanmıyor.
                ok = False
            else:
                ok = True

            with self._lock:
                self._wait_times.append(wait)
                if ok:
                    self._completed += 1
                else:
                    self._failed += 1

    # ------------------------------------------------------------------ yaşam döngüsü

    def shutdown(self, wait: bool = True) -> None:
        """Yeni görevleri reddeder, kuyruktakileri bitirir, worker'ları kapatır.

        wait=False ise worker'ların bitmesi beklenmez ama kuyruk yine de boşaltılır.
        """
        with self._lock:
            already_closed = self._closed
            self._closed = True
            # Bloklanmış üreticiler worker'lar çalıştığı sürece eninde sonunda yer bulur
            # (veya timeout'a düşer), bu yüzden bu bekleme deadlock'a girmez.
            while self._inflight > 0:
                self._no_inflight.wait()

        if not already_closed:
            # Sentinel'ler FIFO sırada mevcut görevlerin arkasına girer: önce kuyruk boşalır.
            # put() bloklayabilir ama worker'lar hâlâ tükettiği için ilerler.
            for _ in self._threads:
                self._queue.put(_SENTINEL)

        if wait:
            for t in self._threads:
                t.join()

    def __enter__(self) -> "WorkerPool":
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown(wait=True)

    # ------------------------------------------------------------------ gözlem

    def stats(self) -> PoolStats:
        with self._lock:
            waits = sorted(self._wait_times)
            snapshot = (
                self._submitted,
                self._completed,
                self._failed,
                self._dropped,
                self._rejected,
            )

        avg = sum(waits) / len(waits) if waits else 0.0
        # Nearest-rank p95: ceil(0.95 * n) - 1 indeksi; interpolasyon yok, küçük n'de de tanımlı.
        p95 = waits[max(0, -(-95 * len(waits) // 100) - 1)] if waits else 0.0
        return PoolStats(
            *snapshot,
            avg_wait_s=avg,
            p95_wait_s=p95,
            # qsize() yaklaşık bir değerdir; gözlem için yeterli, karar mantığında kullanılmamalı.
            queue_length=self._queue.qsize(),
        )