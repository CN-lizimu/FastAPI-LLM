from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
import unicodedata

from langchain_core.documents import Document


_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]+|[a-z0-9]+(?:[._+-][a-z0-9]+)*", re.IGNORECASE)


def tokenize_chinese_news(text: str) -> list[str]:
    """Tokenize mixed Chinese/ASCII news text without an external dictionary."""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(normalized):
        value = match.group(0)
        if "\u4e00" <= value[0] <= "\u9fff":
            if len(value) == 1:
                tokens.append(value)
            else:
                tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
        else:
            tokens.append(value)
    return tokens


@dataclass(frozen=True)
class BM25Hit:
    document: Document
    score: float
    rank: int


class BM25Index:
    """Small in-memory Okapi BM25 index over the existing Chroma chunks."""

    def __init__(self, documents: list[Document], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.documents = documents
        self.k1 = k1
        self.b = b
        self.term_frequencies: list[Counter[str]] = []
        self.document_lengths: list[int] = []
        document_frequencies: Counter[str] = Counter()

        for document in documents:
            title = str(document.metadata.get("title") or "")
            # Repeating title terms gives short, descriptive news titles useful weight.
            tokens = tokenize_chinese_news(f"{title} {title} {document.page_content}")
            frequencies = Counter(tokens)
            self.term_frequencies.append(frequencies)
            self.document_lengths.append(len(tokens))
            document_frequencies.update(frequencies.keys())

        self.average_document_length = (
            sum(self.document_lengths) / len(self.document_lengths) if self.document_lengths else 0.0
        )
        document_count = len(documents)
        self.inverse_document_frequencies = {
            term: math.log(1.0 + (document_count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequencies.items()
        }

    def search(self, query: str, k: int) -> list[BM25Hit]:
        query_terms = list(dict.fromkeys(tokenize_chinese_news(query)))
        if not query_terms or not self.documents or not self.average_document_length:
            return []

        scored: list[tuple[int, float]] = []
        for index, frequencies in enumerate(self.term_frequencies):
            document_length = self.document_lengths[index]
            score = 0.0
            for term in query_terms:
                term_frequency = frequencies.get(term, 0)
                if not term_frequency:
                    continue
                denominator = term_frequency + self.k1 * (
                    1.0 - self.b + self.b * document_length / self.average_document_length
                )
                score += self.inverse_document_frequencies.get(term, 0.0) * (
                    term_frequency * (self.k1 + 1.0) / denominator
                )
            if score > 0:
                scored.append((index, score))

        scored.sort(key=lambda item: (-item[1], item[0]))
        return [
            BM25Hit(document=self.documents[index], score=score, rank=rank)
            for rank, (index, score) in enumerate(scored[:k], start=1)
        ]
