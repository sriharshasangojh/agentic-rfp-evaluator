"""Keyword search over the proposals (feature F14, spec section 12): which pieces of the proposals answer a question?

The proposals are cut into chunks of about 300 words when they are read (`pdf_tool.build_chunks`: a chunk never crosses a page, so a
supplier and a page are an exact citation). This module ranks those chunks against a question with BM25, the usual keyword
ranking, and returns the best few. It is used for questions only: scoring never uses it, the evaluator always reads the whole
proposal (spec 12).

BM25 is the Okapi formula the spec's `rank_bm25` library uses (k1 = 1.5, b = 0.75, epsilon = 0.25, a negative word weight is replaced
by a quarter of the average one). It is written out here, about thirty lines, so the app has one package less to install and the
search can be tested without it; `tests/test_retrieval.py` checks it against `rank_bm25` whenever that package is installed.
One deliberate difference: a word whose weight comes out as zero or below (it is in half the chunks or more, which happens with a
very small set of chunks) gets a small positive weight, `MIN_WEIGHT`, so that a short proposal can still be searched; `rank_bm25`
would give such a word no weight at all and find nothing.

Plain Python, no AI, no database: the same question over the same chunks always gives the same order.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

TOP_K = 5                 # how many excerpts go to the AI when one supplier is searched (spec 12)
PER_SUPPLIER = 3          # when every supplier is searched: the best excerpts of EACH supplier (see `Index.search`)
K1 = 1.5
B = 0.75
EPSILON = 0.25
MIN_WEIGHT = 0.01         # the weight of a word that is in half of the chunks or more, when the formula gives it nothing (see above)
QUESTION_MAX_CHARS = 300

# Words that carry no meaning for a search. Taken out of both the question and the proposals.
STOPWORDS = frozenset(
    "a an and are as at be been but by can could did do does for from had has have how i if in into is it its me my of on or our "
    "she should so than that the their them then there these they this those to us was we were what when where which who whom whose "
    "why will with would you your about also any each every give gives given say says said tell tells".split())

_WORD = re.compile(r"[a-z0-9]+")


class ChunkLike(Protocol):
    @property
    def supplier(self) -> str: ...

    @property
    def page(self) -> int: ...

    @property
    def number(self) -> int: ...

    @property
    def text(self) -> str: ...


def stem(word: str) -> str:
    """A very light stemmer so that "wiping", "wiped" and "wipe" meet: plural s, then -ing / -ed, then a final e."""
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    if len(word) > 5 and word.endswith("ing"):
        word = word[:-3]
    elif len(word) > 4 and word.endswith("ed"):
        word = word[:-2]
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Lower-case words and numbers, without stop words, lightly stemmed. "next-day" gives "next" and "day"."""
    return [stem(w) for w in _WORD.findall(text.lower()) if w not in STOPWORDS]


class BM25Okapi:
    """BM25 (Okapi) over a list of token lists. Same numbers as `rank_bm25.BM25Okapi` with its default settings."""

    def __init__(self, corpus: Sequence[Sequence[str]], k1: float = K1, b: float = B, epsilon: float = EPSILON) -> None:
        self.k1, self.b, self.epsilon = k1, b, epsilon
        self.size = len(corpus)
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = sum(self.doc_len) / self.size if self.size else 0.0
        self.doc_freqs = [Counter(doc) for doc in corpus]
        containing: Counter[str] = Counter()
        for freqs in self.doc_freqs:
            containing.update(freqs.keys())
        self.idf: dict[str, float] = {}
        weak: list[str] = []
        total = 0.0
        for word, count in containing.items():
            value = math.log(self.size - count + 0.5) - math.log(count + 0.5)
            self.idf[word] = value
            total += value
            if value <= 0:
                weak.append(word)
        self.average_idf = total / len(self.idf) if self.idf else 0.0
        for word in weak:
            self.idf[word] = max(self.epsilon * self.average_idf, MIN_WEIGHT)

    def scores(self, query: Iterable[str]) -> list[float]:
        """One score per document. A word the corpus never saw adds nothing; a word repeated in the query counts each time."""
        result = [0.0] * self.size
        for word in query:
            weight = self.idf.get(word)
            if not weight:
                continue
            for index, freqs in enumerate(self.doc_freqs):
                count = freqs.get(word, 0)
                if count:
                    length = self.doc_len[index] / self.avgdl if self.avgdl else 0.0
                    result[index] += weight * count * (self.k1 + 1) / (count + self.k1 * (1 - self.b + self.b * length))
        return result


@dataclass(frozen=True)
class Hit:
    """One excerpt that matched the question, best first. `rank` is 1 for the best."""

    rank: int
    score: float
    supplier: str
    page: int
    number: int
    text: str


class Index:
    """The search index of one run: every chunk of every supplier, ranked together so that a word that is in every proposal
    counts for little and a word that is in one counts for a lot."""

    def __init__(self, chunks: Sequence[ChunkLike]) -> None:
        self.chunks = tuple(chunks)
        self._bm25 = BM25Okapi([tokenize(c.text) for c in self.chunks])

    @property
    def suppliers(self) -> tuple[str, ...]:
        """The suppliers that have text, in the order they were first seen."""
        return tuple(dict.fromkeys(c.supplier for c in self.chunks))

    def supplier_named(self, question: str) -> str | None:
        """The one supplier a question names ("What does Vertexa say about ...?"), by its full name or its first word, or None when it
        names no supplier or more than one. A first word shorter than 4 letters is not used."""
        text = question.lower()
        found = []
        for name in self.suppliers:
            lowered = name.lower()
            first = lowered.split()[0] if lowered.split() else ""
            for variant in {lowered, first if len(first) >= 4 else ""} - {""}:
                if re.search(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])", text):
                    found.append(name)
                    break
        return found[0] if len(found) == 1 else None

    def search(self, question: str, supplier: str | None = None, top_k: int = TOP_K) -> tuple[Hit, ...]:
        """The best chunks for a question. With a `supplier`: its `top_k` best chunks (spec 12). Without one (all suppliers): the
        `PER_SUPPLIER` best chunks of EACH supplier, best first overall. The spec says "top 5 chunks", but over seven proposals the five
        best usually come from one or two suppliers, so a question such as "which suppliers ..." could not be answered for the rest; and
        a question with two facts ("on-site warranty with next-day repair") often has them in two different chunks of one proposal.
        Chunks that share no word with the question are never returned, so a question the proposals do not touch gives no hits.
        When one supplier is searched, the words of its own name are left out of the question ("What does Vertexa say about warranty?"
        searches for "warranty"): inside one proposal the name says nothing about the topic, and as a rare word it would otherwise
        outweigh it. If nothing else is left ("Tell me about Vertexa") the name is used. Ties keep reading order."""
        words = tokenize(question)
        if supplier is not None:
            own = set(tokenize(supplier))
            words = [w for w in words if w not in own] or words
        if not words or not self.chunks or top_k < 1:
            return ()
        scores = self._bm25.scores(words)
        candidates = [i for i, c in enumerate(self.chunks) if (supplier is None or c.supplier == supplier) and scores[i] > 0]
        ranked = sorted(candidates, key=lambda i: (-scores[i], i))
        if supplier is None:
            kept: dict[str, list[int]] = {}
            for i in ranked:
                bucket = kept.setdefault(self.chunks[i].supplier, [])
                if len(bucket) < PER_SUPPLIER:
                    bucket.append(i)
            picked = sorted((i for bucket in kept.values() for i in bucket), key=lambda i: (-scores[i], i))
        else:
            picked = ranked[:top_k]
        return tuple(Hit(rank, round(scores[i], 4), self.chunks[i].supplier, self.chunks[i].page, self.chunks[i].number, self.chunks[i].text)
                     for rank, i in enumerate(picked, start=1))
