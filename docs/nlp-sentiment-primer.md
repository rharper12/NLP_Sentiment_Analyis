# NLP and sentiment analysis: a primer

Written for an engineer who has never done NLP. By the end you should understand what this app
is measuring and why the pipeline steps exist. Nothing here needs more than high-school maths.

## 1. The problem

Sentiment analysis takes a piece of text and predicts a category: positive, negative, neutral,
or mixed in this app. It is a **classification** task: input text, output
one of a fixed set of labels.

Models operate on numerical representations of text. The representation should preserve
information relevant to the label being predicted; preprocessing can remove useful information
as well as irrelevant variation.

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
learn a weight for. Rare features can make learning harder on a small dataset, but they can
still carry useful information, such as emphasis or the target of an opinion.

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
The app provides a **Keep negations** option when stopword removal is selected. Both checkboxes
start unchecked. Compare the actual transformed text when choosing this option; a change in
Comprehend agreement alone does not prove that preprocessing improved or harmed accuracy.

## 5. Modern models: embeddings

Neural models replace the sparse count vector with a dense **embedding**: a few hundred to a few
thousand numbers per document, learned so that similar meanings land close together.
This app does not compute embeddings or embedding drift.

Preprocessing should match the model's expected inputs. Some models are cased, some are uncased,
and many supply their own tokenizer. Lowercasing or stopword removal can help a count-based
representation in some tasks, but those choices should be evaluated rather than assumed useful.

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

When sentiment classes are imbalanced, report per-class results alongside overall accuracy.
Inspect the actual label distribution rather than assuming it from the topic.

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
