# NLP and sentiment analysis: a primer

Written for an engineer who has never done NLP. By the end you should understand what this app
is measuring and why the pipeline steps exist. Nothing here needs more than high-school maths.

## 1. The problem

Sentiment analysis takes a piece of text and predicts an attitude: positive, negative, neutral
(sometimes finer grades, sometimes emotions). It is a **classification** task: input text, output
one of a fixed set of labels.

Computers cannot read. A model only sees numbers. So the entire game is: turn text into numbers
in a way that preserves the signal (attitude) and discards the noise (everything else), then learn
a function from those numbers to labels.

## 2. From text to numbers: the bag of words

The oldest trick still works well on short social-media text. Give every distinct word an index,
then represent a document as a vector of word counts.

```
vocabulary:  [ "movie", "loved", "terrible", "not", "good" ]
"loved the movie"   → [1, 1, 0, 0, 0]
"not good"          → [0, 0, 0, 1, 1]
```

Each position is a **feature**. The set of all distinct words is the **vocabulary**. This is why
the app's impact strip leads with vocabulary size: every unique token is a column the model has to
learn a weight for, and columns that appear once (a URL, a username, "AMAZINGGG") are pure noise.

A refinement, **TF-IDF**, down-weights words that appear in most documents ("the") and up-weights
words that are distinctive to a few. Still a bag of words, still order-blind.

## 3. Why order-blind models need preprocessing

If the model cannot see order or context, preprocessing has to do the work of making equivalent
things look equivalent:

| Raw text | Problem for a bag-of-words model | Step that fixes it |
|---|---|---|
| "Great" vs "great" | two features for one word | lowercase |
| "loved", "loves", "loving" | three features for one idea | lemmatize |
| "the", "a", "of" | high counts, no attitude | stopwords |
| "@user123", "https://t.co/x" | one-off features | punctuation / special characters |
| "" (empty post) | a zero vector that teaches nothing | missing data |

And one step that *creates* the features in the first place: **tokenization**, splitting the
string into units. "don't" → "do" + "n't" is a choice; it makes negation its own token.

## 4. Negation: the classic trap

"not good" is negative. A stopword list that removes "not" turns it into "good", which is positive.
This single mistake can cost more accuracy than every other step gains. It is why the app keeps
negations by default and exposes the toggle: run it both ways and watch the Comprehend agreement
figure drop.

## 5. Modern models: embeddings

Neural models replace the sparse count vector with a dense **embedding**: a few hundred to a few
thousand numbers per document, learned so that similar meanings land close together.
This app does not compute embeddings or embedding drift.

Important consequence: embedding models were trained on raw, cased, punctuated text. Aggressive
preprocessing can *hurt* them. Lowercasing and stopword removal help a bag-of-words model and may
harm a transformer. That is the central tension the app lets you see rather than assume.

## 6. Measuring a classifier

Once you have a model (Task 2), you evaluate on held-out data with a **confusion matrix**:

|                | predicted positive | predicted negative |
|----------------|--------------------|--------------------|
| actual positive | true positive (TP) | false negative (FN) |
| actual negative | false positive (FP) | true negative (TN) |

- **Accuracy** = (TP + TN) / all. Misleading when classes are imbalanced.
- **Precision** = TP / (TP + FP): of the things you called positive, how many were.
- **Recall** = TP / (TP + FN): of the actual positives, how many you caught.
- **F1** = harmonic mean of precision and recall.

Social-media sentiment is usually imbalanced (a murder trial produces mostly negative posts), so
report per-class F1, not accuracy alone.

## 7. Where labels come from

Supervised sentiment models need a label per document. Human review needs consistent criteria
and may still contain disagreements. Comprehend can provide machine labels at scale.
Reviewing its lowest-confidence predictions helps find likely errors, but the selected posts
are not representative of the dataset. Reviewer agreement is not an accuracy ceiling. Evaluate
a trained model against independently labelled, held-out data; keep that test set out of training.

## 8. What this app measures and why

| Number | What it tells you |
|---|---|
| Vocabulary before → after | distinct token counts in each representation; affected by tokenization |
| Tokens per record | how much text remains for the model to use |
| Type–token ratio | vocabulary / total tokens; higher means more unique words per token, i.e. sparser |
| Sentiment agreement | fraction of records where a fixed classifier (Comprehend) gave the same label before and after; lower agreement flags predictions to inspect, not proven loss of meaning |

None of these establish better sentiment accuracy or preserved meaning. Read actual changed
posts and compare downstream performance on a held-out human-labelled test set.
