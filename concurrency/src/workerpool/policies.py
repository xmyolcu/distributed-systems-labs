from enum import Enum


class BackpressurePolicy(Enum):
    """Kuyruk doluyken submit()'in nasıl davranacağı."""

    # Üretici yer açılana kadar bekler; akış kontrolü üreticiye geri yansır.
    BLOCK = "block"
    # Görev sessizce atılır (load shedding); üretici False ile öğrenir.
    DROP = "drop"
    # QueueFull fırlatılır; üretici hatayı açıkça ele almak zorundadır.
    REJECT = "reject"
