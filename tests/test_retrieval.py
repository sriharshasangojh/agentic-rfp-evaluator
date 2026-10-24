"""Tests for the keyword search over the proposals (feature F14, spec section 12): `tools/retrieval.py`. Plain Python: no model, no database.

The proposals are cut into chunks (tested in test_pdf_tool.py); this file checks how a question is turned into words, how BM25 scores
them (against a hand-worked example, and against the `rank_bm25` library when it is installed), and which chunks are handed to the AI.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from agents import orchestrator
from tools import pdf_tool, retrieval
from tools.retrieval import BM25Okapi, Index, stem, tokenize


def chunk(supplier: str, page: int, number: int, text: str) -> pdf_tool.Chunk:
    return pdf_tool.Chunk(supplier, page, number, text)


FILLER = [chunk("Zed Co", n, n, f"unrelated words number{n} alpha{n} beta{n}") for n in range(1, 13)]   # chunks that never match
CHUNKS = [
    chunk("Apex Systems", 1, 1, "Apex offers a three year on-site warranty with next-day repair for every laptop."),
    chunk("Apex Systems", 2, 2, "Delivery takes six weeks. Battery life measured at eleven hours of video playback."),
    chunk("Vertexa Solutions", 1, 1, "Vertexa supplies business laptops with a one year carry-in warranty."),
    chunk("Vertexa Solutions", 3, 2, "Payment terms are thirty days. Prices are valid for ninety days."),
    chunk("NexaWorks", 2, 1, "NexaWorks holds the price for orders placed next year. Warranty is on-site, repair next business day."),
]


# ---------------------------------------------------------------- words

@pytest.mark.parametrize("word, expected", [
    ("wipe", "wip"), ("wiping", "wip"), ("wiped", "wip"), ("wipes", "wip"),          # one stem for every form
    ("warranty", "warranty"), ("warranties", "warranty"), ("batteries", "battery"),
    ("repair", "repair"), ("repairs", "repair"), ("repaired", "repair"), ("repairing", "repair"),
    ("price", "pric"), ("prices", "pric"), ("pricing", "pric"),
    ("measure", "measur"), ("measured", "measur"), ("measuring", "measur"),
    ("business", "business"), ("laptop", "laptop"), ("bus", "bus"), ("the", "the"), ("ties", "tie"),
    ("thing", "thing"), ("need", "need"), ("bed", "bed"),                              # short words that only look like they have an ending
])
def test_a_word_is_cut_to_a_common_stem(word, expected):
    assert stem(word) == expected


def test_a_question_becomes_lower_case_words_without_filler_words():
    assert tokenize("Which suppliers give on-site warranty with next-day repair?") == ["supplier", "sit", "warranty", "next", "day", "repair"]


def test_numbers_are_kept_and_punctuation_is_dropped():
    assert tokenize("Rs 59,000 per laptop (GST: 18%)") == ["rs", "59", "000", "per", "laptop", "gst", "18"]


@pytest.mark.parametrize("text", ["", "   ", "the of and", "?!..."])
def test_a_text_without_content_words_has_no_words(text):
    assert tokenize(text) == []


def test_the_same_text_always_gives_the_same_words():
    text = "Battery life: 11.5 hours measured with video playback"
    assert tokenize(text) == tokenize(text.upper()) == tokenize(text)


# ---------------------------------------------------------------- the formula

def test_the_score_follows_the_okapi_formula_worked_by_hand():
    corpus = [["laptop", "warranty", "warranty"], ["laptop", "battery"], ["battery", "battery", "battery", "life"]]
    bm25 = BM25Okapi(corpus)
    idf = math.log((3 - 1 + 0.5) / (1 + 0.5))                    # "warranty" is in 1 of 3 documents
    average = 9 / 3                                                # 9 words in 3 documents
    expected = idf * 2 * (1.5 + 1) / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / average))   # two mentions in a document of average length
    scores = bm25.scores(["warranty"])
    assert scores[0] == pytest.approx(expected) and scores[0] == pytest.approx(0.72971, abs=1e-4)
    assert scores[1] == scores[2] == 0


def test_the_length_of_the_document_is_worked_in_as_the_formula_says():
    corpus = [["battery", "life"], ["life", "x", "y", "z", "w", "v"], ["o1"], ["o2"], ["o3"]]       # lengths 2, 6, 1, 1, 1: the average is 2.2
    bm25 = BM25Okapi(corpus)
    battery = math.log((5 - 1 + 0.5) / (1 + 0.5))                                                  # in 1 of 5 documents
    life = math.log((5 - 2 + 0.5) / (2 + 0.5))                                                     # in 2 of 5 documents
    expected_short = battery * 1 * 2.5 / (1 + 1.5 * (1 - 0.75 + 0.75 * 2 / 2.2))
    assert bm25.scores(["battery"])[0] == pytest.approx(expected_short) == pytest.approx(1.14547, abs=1e-5)
    both = bm25.scores(["life"])
    assert both[0] == pytest.approx(life * 2.5 / (1 + 1.5 * (1 - 0.75 + 0.75 * 2 / 2.2)))
    assert both[1] == pytest.approx(life * 2.5 / (1 + 1.5 * (1 - 0.75 + 0.75 * 6 / 2.2)))


def test_a_longer_document_scores_lower_for_the_same_word_count():
    bm25 = BM25Okapi([["battery", "life"], ["battery", "life", "x", "y", "z", "w"], ["other"]])
    short, long_, _ = bm25.scores(["battery"])
    assert short > long_ > 0


def test_more_mentions_score_higher_but_with_diminishing_returns():
    bm25 = BM25Okapi([["warranty"] * n + ["pad"] * (6 - n) for n in (1, 2, 3, 4)] + [["other", "words"]] * 8)
    scores = bm25.scores(["warranty"])[:4]
    assert scores[0] < scores[1] < scores[2] < scores[3]
    assert scores[1] - scores[0] > scores[3] - scores[2]


def test_a_word_in_most_documents_never_scores_below_zero():
    corpus = [["common", "x"], ["common", "y"], ["common", "z"], ["rare", "common"]]
    bm25 = BM25Okapi(corpus)
    assert all(weight > 0 for weight in bm25.idf.values())
    assert all(score > 0 for score, doc in zip(bm25.scores(["common"]), corpus) if "common" in doc)


def test_a_negative_weight_is_replaced_by_a_quarter_of_the_average_weight():
    corpus = [["common"] + [f"rare{d}{k}" for k in range(6)] for d in range(3)]     # "common" is in all 3 documents
    bm25 = BM25Okapi(corpus)
    rare, common = math.log(2.5) - math.log(1.5), math.log(0.5) - math.log(3.5)
    average = (18 * rare + common) / 19
    assert common < 0 and average > 0
    assert bm25.idf["common"] == pytest.approx(0.25 * average)
    assert bm25.idf["rare00"] == pytest.approx(rare)


def test_a_weight_of_nothing_or_less_becomes_a_small_positive_one():
    corpus = [["common", "alpha"], ["common", "beta"], ["common", "gamma"]]     # the average weight is below zero here
    assert BM25Okapi(corpus).idf["common"] == retrieval.MIN_WEIGHT
    half = BM25Okapi([["a", "b"], ["c"]])                                       # "a" is in half of the documents: weight 0 by the formula
    assert half.idf["a"] == retrieval.MIN_WEIGHT and half.scores(["a"])[0] > 0


def test_a_very_small_set_of_chunks_can_still_be_searched():
    assert BM25Okapi([["warranty", "five", "years"]]).scores(["warranty"])[0] > 0
    assert Index([chunk("A", 1, 1, "Five years of warranty.")]).search("warranty")[0].page == 1


def test_a_word_that_is_in_no_document_adds_nothing_and_a_repeated_query_word_counts_each_time():
    bm25 = BM25Okapi([["laptop", "warranty"], ["laptop"], ["battery"]])
    assert bm25.scores(["unknown"]) == [0.0, 0.0, 0.0]
    once, twice = bm25.scores(["warranty"]), bm25.scores(["warranty", "warranty"])
    assert twice[0] == pytest.approx(2 * once[0])


def test_an_empty_corpus_scores_nothing_and_does_not_crash():
    assert BM25Okapi([]).scores(["x"]) == []
    assert BM25Okapi([[], []]).scores(["x"]) == [0.0, 0.0]


def test_the_numbers_are_the_same_as_the_rank_bm25_library_when_it_is_installed():
    rank_bm25 = pytest.importorskip("rank_bm25")
    corpus = [tokenize(c.text) for c in CHUNKS]
    for query in ("warranty repair", "battery measured hours", "price year", "payment days"):
        ours = BM25Okapi(corpus).scores(tokenize(query))
        theirs = rank_bm25.BM25Okapi(corpus).get_scores(tokenize(query))
        assert ours == pytest.approx(list(theirs))


# ---------------------------------------------------------------- the search

def test_the_best_chunk_comes_first_and_ranks_count_from_one():
    hits = Index(CHUNKS).search("battery life measured", "Apex Systems")
    assert [(h.rank, h.page) for h in hits] == [(1, 2)]
    assert hits[0].supplier == "Apex Systems" and hits[0].number == 2 and hits[0].text == CHUNKS[1].text
    assert hits[0].score == round(Index(CHUNKS)._bm25.scores(tokenize("battery life measured"))[1], 4) and hits[0].score > 0


def test_one_supplier_gives_only_its_own_chunks_at_most_five():
    many = [chunk("Apex Systems", n, n, f"warranty item {n} " + "x " * n) for n in range(1, 9)] + [chunk("Other Co", 1, 1, "warranty also here")]
    hits = Index(many).search("warranty", "Apex Systems")
    assert len(hits) == retrieval.TOP_K == 5
    assert {h.supplier for h in hits} == {"Apex Systems"}
    assert [h.rank for h in hits] == [1, 2, 3, 4, 5]
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_all_suppliers_gives_the_three_best_chunks_of_each_supplier_best_first():
    many = [chunk(name, n, n, f"warranty {'warranty ' * (4 - n)} filler {n}") for name in ("Apex", "Beta", "Cora") for n in (1, 2, 3, 4)]
    hits = Index(many + FILLER).search("warranty")
    assert retrieval.PER_SUPPLIER == 3 and len(hits) == 9 == 3 * retrieval.PER_SUPPLIER
    assert sorted(h.supplier for h in hits) == ["Apex"] * 3 + ["Beta"] * 3 + ["Cora"] * 3
    assert all(h.page in (1, 2, 3) for h in hits)                    # the fourth chunk of each is the weakest and is left out
    assert [h.rank for h in hits] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_all_suppliers_still_hears_from_a_supplier_whose_chunks_are_weaker():
    chunks = [chunk("Strong", n, n, "warranty " * 8 + f"extra {n}") for n in range(1, 8)] + [chunk("Weak", 1, 1, "a short note on warranty")]
    names = [h.supplier for h in Index(chunks + FILLER).search("warranty")]
    assert "Weak" in names and names.count("Strong") == retrieval.PER_SUPPLIER == 3


def test_all_suppliers_leaves_out_a_supplier_that_shares_no_word_with_the_question():
    hits = Index(CHUNKS).search("battery hours")
    assert {h.supplier for h in hits} == {"Apex Systems"}


def test_a_question_that_shares_no_word_with_any_chunk_gives_no_hits():
    index = Index(CHUNKS)
    assert index.search("quantum entanglement") == () and index.search("quantum entanglement", "Apex Systems") == ()


@pytest.mark.parametrize("question", ["", "   ", "the of and", "?!", "What is it?"])
def test_a_question_without_content_words_gives_no_hits(question):
    assert Index(CHUNKS).search(question) == ()


def test_there_is_nothing_to_find_in_no_chunks_or_with_no_room_for_hits():
    assert Index([]).search("warranty") == ()
    assert Index(CHUNKS).search("warranty", "Apex Systems", top_k=0) == ()
    assert Index(CHUNKS).search("warranty", "Apex Systems", top_k=-1) == ()
    assert Index(CHUNKS).search("warranty", top_k=0) == () and Index(CHUNKS).search("warranty", top_k=-1) == ()     # all suppliers too
    assert len(Index(CHUNKS).search("warranty", "Apex Systems", top_k=1)) == 1


def test_a_supplier_that_is_not_in_the_index_gives_no_hits():
    assert Index(CHUNKS).search("warranty", "Nobody Ltd") == ()


def test_equal_scores_keep_reading_order():
    twins = [chunk("A", 1, 1, "warranty five years"), chunk("A", 2, 2, "warranty five years"), chunk("A", 3, 3, "warranty five years")]
    assert [h.page for h in Index(twins + FILLER).search("warranty", "A")] == [1, 2, 3]
    assert [h.page for h in Index(twins).search("warranty", "A")] == [1, 2, 3]       # also when the weights are all the small floor


def test_equal_scores_across_suppliers_keep_reading_order_too():
    twins = [chunk("A", 1, 1, "warranty five years"), chunk("B", 1, 1, "warranty five years"), chunk("C", 1, 1, "warranty five years")]
    assert [h.supplier for h in Index(twins + FILLER).search("warranty")] == ["A", "B", "C"]


def test_the_same_question_over_the_same_chunks_always_gives_the_same_hits():
    assert Index(CHUNKS).search("warranty repair") == Index(list(CHUNKS)).search("warranty repair")


def test_other_forms_of_a_word_find_the_chunk():
    chunks = [chunk("A", 1, 1, "Drives are wiped to a named standard."), chunk("A", 2, 2, "Delivery takes six weeks.")]
    assert [h.page for h in Index(chunks + FILLER).search("data wiping", "A")] == [1]


def test_a_word_in_every_proposal_counts_for_less_than_a_word_in_one():
    chunks = ([chunk(f"S{n}", 1, 1, "laptop laptop laptop price") for n in range(5)] + [chunk("Rare", 1, 1, "laptop zirconium")])
    assert Index(chunks).search("laptop zirconium")[0].supplier == "Rare"


def test_the_suppliers_are_listed_in_the_order_they_first_appear():
    assert Index(CHUNKS).suppliers == ("Apex Systems", "Vertexa Solutions", "NexaWorks")
    assert Index(reversed(list(CHUNKS))).suppliers == ("NexaWorks", "Vertexa Solutions", "Apex Systems")


# ---------------------------------------------------------------- a supplier named in the question

def test_a_supplier_named_by_its_full_name_or_its_first_word_is_found():
    index = Index(CHUNKS)
    assert index.supplier_named("What does Vertexa say about data wiping?") == "Vertexa Solutions"
    assert index.supplier_named("is vertexa solutions price valid?") == "Vertexa Solutions"
    assert index.supplier_named("Does NEXAWORKS give a warranty?") == "NexaWorks"
    assert index.supplier_named("apex's warranty") == "Apex Systems"


def test_a_question_that_names_two_suppliers_or_none_is_not_narrowed():
    index = Index(CHUNKS)
    assert index.supplier_named("Compare Apex and Vertexa on warranty") is None
    assert index.supplier_named("Which supplier has the best warranty?") is None
    assert index.supplier_named("") is None


def test_only_whole_words_name_a_supplier():
    index = Index(CHUNKS)
    assert index.supplier_named("What about Apexa?") is None
    assert index.supplier_named("What about Vertexas and Nexaworksy?") is None
    assert index.supplier_named("Is it Apex-branded?") == "Apex Systems"                  # a hyphen ends a word
    assert index.supplier_named("an apex predator") == "Apex Systems"     # a limit of keywords: the word is there


def test_a_short_first_word_is_not_used_to_find_a_supplier_but_the_full_name_is():
    index = Index([chunk("AB Corp", 1, 1, "warranty"), chunk("Other", 1, 1, "warranty")])
    assert index.supplier_named("what does ab say") is None
    assert index.supplier_named("what does AB Corp say") == "AB Corp"


def test_the_suppliers_own_name_is_left_out_of_the_words_searched_in_its_proposal():
    chunks = [chunk("Vertexa Solutions", 5, 1, "Vertexa Vertexa Vertexa company history of Vertexa Solutions."),
              chunk("Vertexa Solutions", 1, 2, "One year carry-in warranty."),
              chunk("Apex Systems", 1, 1, "Three year warranty."), chunk("Apex Systems", 2, 2, "Five year warranty on batteries."),
              chunk("Beta Co", 1, 1, "Two year warranty."), chunk("Beta Co", 2, 2, "Warranty claims take a week.")]
    index = Index(chunks)
    assert index.search("Vertexa warranty")[0].page == 5                       # searched among everyone, the rare name wins
    assert index.search("Vertexa warranty", "Vertexa Solutions")[0].page == 1  # inside the one proposal, the topic does


def test_when_only_the_name_is_asked_the_name_is_used():
    chunks = [chunk("Vertexa Solutions", 1, 1, "Vertexa builds laptops."), chunk("Apex Systems", 1, 1, "Apex builds laptops too.")]
    hits = Index(chunks).search("Tell me about Vertexa", "Vertexa Solutions")
    assert [h.supplier for h in hits] == ["Vertexa Solutions"]


# ---------------------------------------------------------------- the real sample proposals

PROPOSALS = Path(__file__).resolve().parents[1] / "data" / "proposals"
needs_samples = pytest.mark.skipif(not pdf_tool.available_backends() or not PROPOSALS.is_dir() or len(list(PROPOSALS.glob("*.pdf"))) < 7,
                                   reason="the 7 sample proposals or a PDF reader are not available")


@pytest.fixture(scope="module")
def samples() -> Index:
    chunks: list[pdf_tool.Chunk] = []
    for path in sorted(PROPOSALS.glob("*.pdf")):
        extracted = pdf_tool.extract_pdf(path.read_bytes())
        chunks.extend(pdf_tool.build_chunks(extracted.pages, orchestrator.default_supplier_name(path.name)))
    return Index(chunks)


@needs_samples
@pytest.mark.parametrize("question", ["Which suppliers give on-site warranty with next-day repair?",
                                      "Who will hold the same price for laptops we order next year?",
                                      "What battery life did each supplier actually measure?"])
def test_each_demo_question_finds_excerpts_in_every_proposal(samples, question):
    hits = samples.search(question, samples.supplier_named(question))
    assert {h.supplier for h in hits} == set(samples.suppliers) and len(samples.suppliers) == 7
    assert len(hits) <= retrieval.PER_SUPPLIER * len(samples.suppliers)
    assert all(h.page >= 1 and h.text.strip() for h in hits)


@needs_samples
def test_the_price_hold_sentences_are_among_the_excerpts_of_demo_question_two(samples):
    """Apex and NexaWorks state the 12-month price hold in the THIRD best chunk for this question; with two per supplier it was left out."""
    hits = samples.search("Who will hold the same price for laptops we order next year?")
    held = {h.supplier.lower() for h in hits if "12 months from the date of this proposal" in " ".join(h.text.split())}
    assert held == {"apex systems", "nexaworks"}


@needs_samples
def test_the_vertexa_data_wiping_question_names_vertexa_and_finds_nothing_about_wiping(samples):
    question = "What does Vertexa say about data wiping?"
    assert samples.supplier_named(question) == "Vertexa Solutions"
    hits = samples.search(question, "Vertexa Solutions")
    assert all(not any(word in h.text.lower() for word in ("wipe", "wiping", "erase", "sanitis", "destruction")) for h in hits)
