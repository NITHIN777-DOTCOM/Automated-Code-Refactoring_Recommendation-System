"""Plain-language content for `refactor-scan explain`, and the phrasing used
by the --audience simple report. Kept separate from the rendering code so the
wording can be edited without touching any rich/click plumbing."""

from __future__ import annotations

# The only non-wording import in this module, and a deliberate one: the
# "what it looks at" sentence below states how many measurements the
# classifier reads, and that number is defined by these lists. Counting them
# here means adding a column to engine/ml/features.py cannot leave this
# sentence quoting a number no model uses any more.
from engine.ml.features import EXTENDED_FEATURE_COLUMNS, FEATURE_COLUMNS

SMELL_EXPLANATIONS = {
    "God Class": {
        "aka": "Blob, Large Class",
        "what": (
            "A class that has grown to do far too much. Instead of one clear job, "
            "it ends up owning several unrelated responsibilities -- managing "
            "profile data AND passwords AND sending emails, for example -- all in "
            "one place."
        ),
        "why_it_matters": (
            "Every unrelated responsibility crammed into one class makes it harder "
            "to understand, harder to test in isolation, and riskier to change: a "
            "tweak to the email logic can accidentally break the password logic "
            "sitting right next to it. It also tends to attract even more code over "
            "time, since it's already 'the place everything goes'."
        ),
        "fix": (
            "Split the class along its natural seams -- group the methods and data "
            "that belong together into their own smaller classes, each with one job."
        ),
    },
    "Data Class": {
        "aka": "Anemic Class",
        "what": (
            "A class that mostly just holds data -- getters and setters -- with "
            "little or no real behavior of its own. Other classes reach in to read "
            "and write its fields directly rather than asking it to do anything."
        ),
        "why_it_matters": (
            "On its own, a data class isn't necessarily a problem -- some classes "
            "genuinely are just data. But it's often a sign that logic which "
            "belongs with the data has leaked out into whichever class happens to "
            "use it, spreading related behavior across the codebase instead of "
            "keeping it in one place."
        ),
        "fix": (
            "If methods that use this class's fields together tend to live in the "
            "same other class, consider moving that behavior onto the data class "
            "itself. If it really is just a data holder, that can be fine as-is."
        ),
    },
    "Feature Envy": {
        "aka": None,
        "what": (
            "A method that spends more time working with another class's data than "
            "its own -- calling that other class's methods or reading its fields "
            "over and over to do its job."
        ),
        "why_it_matters": (
            "A method that's more interested in someone else's data than its own is "
            "usually in the wrong place. It also tightly couples the two classes "
            "together, so a change to one is more likely to break the other."
        ),
        "fix": (
            "Consider moving the method (or the part of it that does the reaching) "
            "into the class it's so interested in."
        ),
    },
    "Long Method": {
        "aka": None,
        "what": (
            "A single method that tries to do too much -- long, with many "
            "branches and decision points, making it hard to follow in one read."
        ),
        "why_it_matters": (
            "Long, complex methods are harder to understand, harder to test "
            "thoroughly (more paths through the code means more cases to cover), "
            "and harder to change safely."
        ),
        "fix": (
            "Break it into smaller methods, each handling one step or one "
            "decision, and give each a name that says what it does."
        ),
    },
    "Long Parameter List": {
        "aka": None,
        "what": (
            "A method that takes too many parameters (five or more, not counting "
            "self) to call comfortably or remember correctly."
        ),
        "why_it_matters": (
            "Long parameter lists are easy to call wrong -- especially when several "
            "parameters share a type, it's easy to pass values in the wrong order "
            "without the mistake being obvious. They're also a sign that the "
            "parameters themselves form a concept that doesn't have a name yet."
        ),
        "fix": (
            "Group related parameters into a single object or dataclass and pass "
            "that instead of each value individually."
        ),
    },
    "Duplicate Code": {
        "aka": "Copy-Paste Code, Clones",
        "what": (
            "Two methods in the same class that are written almost identically -- "
            "usually one was copied from the other and lightly edited. The check "
            "compares the *structure* of the code (its loops, branches, calls and "
            "nesting) after throwing away every variable name, so a copy whose "
            "variables were all renamed still matches."
        ),
        "why_it_matters": (
            "Copies drift. A bug fixed in one place stays broken in the other, and "
            "a change to the rule they both implement has to be remembered twice. "
            "The cost isn't the extra lines -- it's that nothing links the copies "
            "together, so there's no way to tell from one of them that the other "
            "exists."
        ),
        "fix": (
            "If the two methods really do the same work, pull the shared part into "
            "one method that both call, passing whatever differs between them as a "
            "parameter."
        ),
        "caveat": (
            "This is the least certain of the checks in this tool, and it's the only "
            "one measuring similarity rather than a defined property. It compares "
            "shape, not meaning: methods that follow the same template -- a run of "
            "validators, several methods that each loop over a list and build up a "
            "result -- can score 90%+ while doing completely unrelated work. Expect "
            "more false positives here than anywhere else in this tool, and read "
            "both methods before merging them. A high score is a reason to look, "
            "not a verdict."
        ),
    },
    "Clean": {
        "aka": None,
        "what": (
            "No smell was detected. The class is a reasonable size, its methods "
            "work together on shared data, and it isn't unusually complex or "
            "tangled up with other classes."
        ),
        "why_it_matters": (
            "This is the baseline you're aiming for -- not perfect code, just code "
            "with no obvious structural red flags by the metrics this tool checks."
        ),
        "fix": "Nothing to do here.",
    },
}

# How many structural measurements each classifier reads, counted from the
# feature lists themselves rather than written out. `custom` is None on
# purpose: a user-supplied bundle declares its own column list, and claiming a
# count for it would be a guess.
CLASSIFIER_FEATURE_COUNTS = {
    "synthetic": len(FEATURE_COLUMNS),
    "real": len(EXTENDED_FEATURE_COLUMNS),
    "custom": None,
}

_NUMBER_WORDS = {8: "eight", 11: "eleven"}


def _count_word(count: int) -> str:
    """Spell a small count, falling back to digits for anything unexpected.

    Prose, not arithmetic: the surrounding sentence reads as English, so a
    future ninth or twelfth feature should render as a numeral rather than
    silently keep saying "eight".
    """
    return _NUMBER_WORDS.get(count, str(count))


MODEL_EXPLANATION = {
    "what_it_is": (
        "The classifier is a Random Forest -- a machine learning model made up of "
        "many decision trees (100, in this case). Each tree looks at a class's "
        "measurements and makes its own guess at which code smell (if any) applies; "
        "the forest's final answer is effectively a vote across all 100 trees. "
        "Using many trees instead of one makes the result more stable and less "
        "likely to be thrown off by any single unusual case."
    ),
    "what_it_looks_at": (
        "It doesn't read your code's meaning -- it looks at "
        f"{_count_word(CLASSIFIER_FEATURE_COUNTS['synthetic'])} structural "
        "measurements taken from the code's shape: how many lines the class and "
        "its methods span, how complex each method's logic is (how many branches "
        "and loops it has), how many other classes it's coupled to, how deep its "
        "inheritance chain is, and -- most importantly -- how much its methods "
        "share data with each other versus working on unrelated pieces of state."
    ),
    "confidence": (
        "Confidence is the model's own certainty in its top answer -- literally "
        "the fraction of the 100 trees that agreed on that label. If 60 of the 100 "
        "trees voted 'God Class' and the rest were split among other labels, the "
        "reported confidence is 60%. A low confidence score is worth paying "
        "attention to -- it usually means the class sits in an ambiguous, "
        "borderline region rather than looking clearly like one smell or another."
    ),
    "caveat": (
        "The model was trained on synthetic, programmatically generated example "
        "classes, not real-world code. It's a useful first pass, but treat its "
        "labels as a starting point for a human to review, not a verdict."
    ),
}

# The "caveat" above is written for the shipped `synthetic` classifier and is
# wrong for any other model. `explain --model --classifier NAME` swaps in the
# matching text: the synthetic entry here is byte-identical to
# MODEL_EXPLANATION["caveat"] so the default output never changes.
CLASSIFIER_CAVEATS = {
    "synthetic": MODEL_EXPLANATION["caveat"],
    "real": (
        "This model was trained on 9,151 real-world Python files -- the ETH Py150 "
        "dataset, 44 open-source GitHub repositories, installed "
        "site-packages, and the CodeSearchNet Python corpus. Its labels were not "
        "hand-assigned: each class was measured against published thresholds from "
        "the code-smell literature (Lanza & Marinescu 2006; McCabe 1976), so its "
        "predictions reflect agreement with those rules rather than human "
        "judgement. It is reliable on ordinary 'Clean' code and on the two smells "
        "with clear structural signatures -- God Class and Long Method -- but the "
        "real corpus is around 86% Clean, so Feature Envy stays weak and there "
        "were too few Data Class examples to measure that behaviour at all. Treat "
        "its labels as a starting point for a human to review, not a verdict. See "
        "RETRAIN_COMPARISON.md for the full side-by-side comparison."
    ),
    "custom": (
        "This is a custom, user-provided classifier loaded from a .joblib bundle. "
        "No training provenance is available for it, so the data it was trained "
        "on, its accuracy and its blind spots are all unknown. Treat its labels "
        "as a starting point for a human to review, not a verdict."
    ),
}

# The "what it looks at" sentence in MODEL_EXPLANATION counts the DEFAULT
# classifier's features. The real-corpus model was fitted on the same eight
# plus real ATFD/FDP, so the count -- like the caveat above -- has to follow
# the selected classifier. Keyed identically to CLASSIFIER_CAVEATS so that one
# resolution in main.py picks both, and the synthetic entry is the same object
# as MODEL_EXPLANATION["what_it_looks_at"] so the default output never changes.
CLASSIFIER_FEATURE_SUMMARIES = {
    "synthetic": MODEL_EXPLANATION["what_it_looks_at"],
    "real": (
        "It doesn't read your code's meaning -- it looks at "
        f"{_count_word(CLASSIFIER_FEATURE_COUNTS['real'])} structural "
        "measurements taken from the code's shape: the eight the default model "
        "uses -- how many lines the class and its methods span, how complex each "
        "method's logic is, how many other classes it's coupled to, how deep its "
        "inheritance chain is, and how much its methods share data with each "
        "other -- plus three that measure how far it reaches into other objects' "
        "data: how many foreign attributes it touches (ATFD), how many different "
        "classes those come from (FDP), and how concentrated it is on any one of "
        "them."
    ),
    "custom": (
        "It doesn't read your code's meaning -- it looks at whichever structural "
        "measurements its own bundle was fitted on. How many, and which, is "
        "recorded in that bundle rather than here, so this explanation cannot "
        "state them for a classifier it did not train."
    ),
}
