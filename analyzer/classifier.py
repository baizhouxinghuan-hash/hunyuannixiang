"""注意：任务书 §4 用 ``request_classifier.py``，§38 用 ``classifier.py``。
两者指向同一实现，这里做别名导出，避免出现两份规则。
"""

from analyzer.request_classifier import (
    CLASSIFICATION_LABELS,
    Classification,
    classify,
    classify_records,
)

__all__ = ["CLASSIFICATION_LABELS", "Classification", "classify", "classify_records"]
