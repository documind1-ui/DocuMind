from preprocessing.clean_text import clean_text
from preprocessing.chunking import chunk_text

sample = "This is   a test document.   It has extra spaces."

cleaned = clean_text(sample)
chunks = chunk_text(cleaned)

print(cleaned)
print(chunks)