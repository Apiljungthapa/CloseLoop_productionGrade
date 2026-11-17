"""Keyword rules for the intent router. Edit here to tune routing."""

CHITCHAT = {"hi", "hello", "hey", "thanks", "thank you", "good morning", "good evening", "bye",
    "who are you", "who r u", "what are you", "what is your name", "whats your name", "what's your name",
    "your name", "tell me about yourself", "introduce yourself", "what can you do", "help",
    "namaste", "hii", "hiii", "yo", "sup"}

# Phrases that suggest a question about live records rather than documents
DB_HINTS = (
    "how many", "count of", "total number", "number of", "list all", "list of",
    "latest", "most recent", "overdue", "pending", "assigned to", "status of",
    "this week", "this month", "last month", "today", "yesterday", "average", "sum of",
    "who is", "who are", "whose", "show me", "do we have",
)

# Phrases that suggest a question about documentation/policy
DOC_HINTS = (
    "policy", "explain", "describe", "how do i", "how to", "what is", "what does",
    "guide", "process", "procedure", "why", "steps", "rules", "documentation",
)
