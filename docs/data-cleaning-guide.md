# Choosing preprocessing steps

Start with the dataset and the intended model. Every Clean-screen checkbox is initially off;
select a step only when its benefit justifies the information it removes. Original text is
preserved, so you can compare different configurations.

## Lowercasing

Lowercasing combines capitalization variants such as `Great`, `great`, and `GREAT`. That can
reduce sparse features in a word-count or TF-IDF representation. It also removes emphasis and
case distinctions that may matter to sentiment or a cased model. Inspect posts with capitals
before selecting it.

## Punctuation and special characters

This step removes URLs, mentions, punctuation, and non-word symbols, including emojis. Hashtag
words remain. Those removals can reduce incidental vocabulary, but they can also discard emotion,
questions, and the target or context of an opinion. Preserving these features is a reasonable
starting point for sentiment analysis of short posts.

## Tokenization

The app uses NLTK's Treebank tokenizer. It creates a token list and replaces the processed text
with those tokens joined by spaces. Contractions can become separate components, such as `do`
and `n't`. URLs and hashtags can also split into fragments.

Use this step when that representation suits subsequent analysis. A model with its own tokenizer
may need the original text instead. Changes in vocabulary or token counts after tokenization
reflect a change in how text is counted; they do not establish improved accuracy.

## Stopword removal

Removing frequent function words can reduce the size of a count-based representation. It can
also remove useful context, intensity, or negation. If you select this step, consider selecting
**Keep negations** and inspect the resulting phrases. That option starts unchecked, like the
other Clean-screen options. Its implementation preserves a defined set of negation forms;
it does not interpret every possible negated expression.

## Lemmatization

Lemmatization groups inflected words into dictionary forms. The app uses WordNet with
part-of-speech tags. This can pool evidence for frequency-based models, but tagging errors and
ambiguous forms can produce unsuitable lemmas. It also removes some tense and number information.
Review changed words before using this representation.

## Empty-record handling

When selected, this step runs last to catch text emptied by earlier transformations. **Drop**
removes those records; **Fill** preserves their IDs and supplies a placeholder. Filling introduces
artificial text, and dropping may change the sample's composition. Report the number affected.
A non-empty record can still be irrelevant, misleading, or difficult to interpret.

Collection checks are separate. CSV import skips blank text; X collection applies a five-token
minimum after excluding links and mentions from the length check. That heuristic can exclude
meaningful short posts. Deduplication also runs at collection when enabled. Neither check proves
that retained posts are relevant or representative.

## Comparing configurations

For an initial tweet experiment, compare unchanged text with a small configuration such as
lowercasing, tokenization, and final empty-record handling. Leave punctuation removal, stopword
removal, and lemmatization off until you have a reason to add them. This is a starting experiment,
not a universal recipe.

Use Analyze to inspect actual text differences as well as the counts. Comprehend agreement means
that predictions stayed the same, not that either prediction was correct. Assess sentiment
accuracy separately using independently labelled, held-out data and a consistent label definition.
Do not treat a low-confidence review queue as a representative test set.

Comprehend labelling in Step 4 uses original text. The optional comparisons in Step 3 score both
original and processed text. Keep that distinction clear when explaining observed errors.
