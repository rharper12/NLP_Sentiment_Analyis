# Data cleaning guide: which technique, and when

Preprocessing is not a checklist to run in full every time. Each step trades signal for
simplicity. This guide gives a decision for each step in this app, then a recipe by scenario.

## Decide by model first

| Your model | Lowercase | Punctuation | Stopwords | Lemmatize |
|---|---|---|---|---|
| Bag of words / TF-IDF + logistic regression, naive Bayes, SVM | yes | yes | yes, keep negations | usually yes |
| Word embeddings averaged (word2vec, GloVe) | yes | yes | often | no (embeddings already generalise) |
| Transformer / LLM (BERT, Titan, Claude) | **no** (cased models) | mostly no | **no** | **no** |

Tokenization and missing-data handling are always needed; the question is only *how*.

## Step by step

### Handle empty records — last, not first
Social-media posts almost never *arrive* empty: the X API always returns text, and a media-only
tweet's text is its `t.co` link. They **become** empty — a link-and-mention post has nothing left
once URLs and mentions are stripped, an emoji-only post nothing after special characters, "I am
what I am" nothing after stopwords. So the sweep belongs at the end of the pipeline, where the
emptying happens, not at the start where it cannot see it.

Two different problems, handled in two different places:

1. **No content on arrival** (a post that is only a link) is filtered at *collection*, by counting
   tokens after links and mentions are removed. Doing it there keeps the record count identical
   across every preprocessing configuration you try, so two runs are compared against the same
   denominator. Report how many were dropped and why.
2. **Emptied by the steps** is what the pipeline's final sweep handles.

**Drop** is the right default. Empty or whitespace-only text produces zero vectors and API errors. **Fill** with a placeholder when row count matters (paired
data, fixed-size batches) or when you want to study how much was missing. Watch for missingness
that correlates with a label: image-only posts are often positive; dropping them skews the class
balance. Report the count either way.

### Lowercase — for count-based models
Merges "Great"/"great"/"GREAT". Shrinks vocabulary and sparsity. **Skip** for cased transformers,
and consider skipping when emphasis matters: ALL CAPS is a strong sentiment cue on social media.
A compromise some teams use: lowercase but add a feature for "fraction of capitals".

### Punctuation and special characters — almost always, carefully
URLs and @mentions carry no sentiment and each is a unique token: remove them. Hashtags usually
carry the word (#disappointed): keep the word, drop the `#`. Exclamation marks and emoticons
(":(" , "!!!") *do* carry sentiment; this app removes them for simplicity and says so in the
report. If you keep them, treat each as a token rather than leaving it glued to a word.

### Tokenize — always, but choose the tokenizer
Whitespace split is wrong for "movie." and "don't". Treebank (used here) splits contractions,
which makes negation a token ("n't"). For tweets, NLTK's `TweetTokenizer` preserves emoticons and
hashtags and shortens "soooo" to "sooo". For transformers, use the model's own tokenizer and skip
everything else in this section.

### Stopwords — for count-based models, and never blindly
Removing "the", "of", "is" reduces noise for frequency-based models. Two rules:
1. **Keep negations** ("not", "no", "never", "n't"). Removing them flips polarity.
2. Inspect the list. NLTK's English list contains "very", "too", "only" (intensity words) and
   "against". Whether those are noise depends on the task.
Skip for transformers: they use function words for syntax.

### Lemmatize — for small datasets with count-based models
"loved"/"loves"/"loving" → "love" pools evidence when data is scarce. Needs part-of-speech tags to
work well (this app tags with NLTK's perceptron tagger). **Stemming** is the cheap cousin: it
chops suffixes by rule ("studies" → "studi"), faster but produces non-words. Neither helps
transformers. Watch for tense loss: "was great" vs "is great" can matter in reviews.

## Techniques not in this app, and when you would add them

| Technique | Use when |
|---|---|
| Spelling normalisation ("soooo" → "so") | user-generated text, count-based model |
| Emoji to text (😀 → ":grinning_face:") | emoji carry sentiment and you want them as tokens |
| Language filtering | multilingual sources; this app filters X to `lang:en` at fetch time |
| Deduplication / near-duplicate removal | retweets, copy-paste campaigns; inflates confidence |
| Handling class imbalance (re-weighting, resampling) | one label dominates (trial coverage) |
| Named-entity masking ("Lindsay Clancy" → PERSON) | names leak the topic and the model memorises them |
| Minimum length filter | very short posts ("lol", "this") are mostly noise; this app drops < 5 tokens at X fetch |

## Recipes

**Tweets about a live topic, logistic regression on TF-IDF (Task 2 baseline).**
lowercase → punctuation → tokenize → stopwords(keep negations) → lemmatize → missing_data(drop).
Measure vocabulary and sentiment changes on your own dataset; these are not guaranteed outcomes.

**Same tweets, fine-tuning a transformer.**
missing_data(drop) → dedupe → *stop*. Let the model's tokenizer do the rest. Run this app's
pipeline only to *inspect* the data, not to feed the model.

**Product reviews (longer, better spelled).**
lowercase → punctuation → tokenize → stopwords → lemmatize → missing_data. Consider keeping
intensity words ("very", "too") by removing them from the stopword list.

**Comparing techniques for a write-up (Task 1).**
Run the full pipeline, then re-run with one step off at a time. The waterfall shows each step's
vocabulary effect; sentiment agreement measures prediction consistency. Export the report after each
run; the differences are your "strengths and limitations" evidence.

## How to read the app's numbers when deciding

- Vocabulary dropped and agreement stayed high → inspect what was removed; these numbers alone
  do not prove noise reduction or preserved meaning.
- Vocabulary barely moved → the step is not doing much on this data; consider dropping it to
  keep the pipeline simple.
- Agreement fell noticeably → predictions changed. Look at sample diffs in the Records tab
  for what changed; negation removal and over-aggressive lemmatization are the usual culprits.
- Tokenization can increase counts by splitting punctuation and words differently. Compare
  representations consistently and evaluate accuracy separately on held-out human-labelled data.
