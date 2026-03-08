
from llm_namer import get_word_frequencies, log_frequent_words

def test_logging():
    titles = [
        "Apple iPhone 15 Pro Max 256GB Black Titanium",
        "Apple iPhone 15 Pro Max 256GB Blue Titanium",
        "Apple iPhone 15 Pro 128GB Black Titanium",
        "Samsung Galaxy S24 Ultra Black",
        "Samsung Galaxy S24 Ultra Gray",
        "Apple iPhone 14 Pro Max",
        "Sony PlayStation 5 Console",
        "Sony PlayStation 5 Digital Edition"
    ]
    
    print("--- Testing Japanese Frequency Log ---")
    freq_data = get_word_frequencies(titles)
    log_frequent_words(freq_data, "国内")
    
    print("\n--- Testing English Frequency Log ---")
    log_frequent_words(freq_data, "海外")

if __name__ == "__main__":
    test_logging()
