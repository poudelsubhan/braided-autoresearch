"""Word n-gram top-k counter. Correctness contract is in README.md.

Optimize throughput freely; outputs must stay byte-identical.
"""

STOPWORDS = [
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "of", "to",
    "in", "on", "at", "by", "for", "with", "from", "as", "is", "are", "was",
    "were", "be", "been", "being", "it", "its", "this", "that", "these",
    "those", "he", "she", "they", "we", "you", "i", "his", "her", "their",
    "our", "your", "my", "me", "him", "them", "us", "not", "no", "so", "do",
    "does", "did", "have", "has", "had", "will", "would", "can", "could",
    "may", "might", "shall", "should", "there", "here", "when", "where",
    "what", "which", "who", "whom", "how", "why", "all", "any", "each",
    "some", "more", "most", "other", "into", "over", "under", "again",
    "once", "than", "too", "very", "just", "about", "up", "down", "out",
]


def tokenize(text):
    tokens = []
    token = ""
    for ch in text:
        if ch.isalnum():
            token = token + ch.lower()
        else:
            if token != "":
                if token not in STOPWORDS:
                    tokens.append(token)
            token = ""
    if token != "":
        if token not in STOPWORDS:
            tokens.append(token)
    return tokens


def ngrams_of(tokens, n):
    grams = []
    for i in range(len(tokens) - n + 1):
        gram = ""
        for j in range(n):
            if gram == "":
                gram = tokens[i + j]
            else:
                gram = gram + " " + tokens[i + j]
        grams.append(gram)
    return grams


def count_ngrams(docs, n):
    keys = []
    counts = []
    for doc in docs:
        tokens = tokenize(doc)
        for gram in ngrams_of(tokens, n):
            if gram in keys:
                idx = keys.index(gram)
                counts[idx] = counts[idx] + 1
            else:
                keys.append(gram)
                counts.append(1)
    return keys, counts


def top_ngrams(docs, n=2, k=50):
    keys, counts = count_ngrams(docs, n)
    remaining_keys = list(keys)
    remaining_counts = list(counts)
    result = []
    while len(result) < k and len(remaining_keys) > 0:
        best_idx = 0
        for i in range(len(remaining_keys)):
            if remaining_counts[i] > remaining_counts[best_idx]:
                best_idx = i
            elif remaining_counts[i] == remaining_counts[best_idx]:
                if remaining_keys[i] < remaining_keys[best_idx]:
                    best_idx = i
        result.append((remaining_keys[best_idx], remaining_counts[best_idx]))
        del remaining_keys[best_idx]
        del remaining_counts[best_idx]
    return result
