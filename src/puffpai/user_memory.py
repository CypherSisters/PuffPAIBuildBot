"""
PuffPAI User Memory — lightweight per-user fact and preference tracker.

Stores conversation summaries and extracted facts so the LLM can
personalize responses across sessions.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime


class UserMemory:
    """Per-user memory that persists across conversations.

    Stores:
      - topics: Counter of discussed topics (extracted from messages)
      - facts: List of user-stated facts ("I have a dog", "I'm allergic to...")
      - preferences: Dict of preferences (response_style, interests, etc.)
      - recent_topics: Last 5 conversation topics for continuity
    """

    _FACT_PATTERNS = [
        # "I am/I'm ..." patterns
        (r"(?:i am|i'm|im)\s+(?:a\s+)?(.{3,40}?)(?:\.|,|!|\?|$)", "identity"),
        # "I have ..." patterns
        (r"(?:i have|i've got)\s+(.{3,40}?)(?:\.|,|!|\?|$)", "possession"),
        # "I like/love/enjoy ..." patterns
        (r"(?:i (?:like|love|enjoy|prefer))\s+(.{3,40}?)(?:\.|,|!|\?|$)", "preference"),
        # "I don't like/hate ..." patterns
        (r"(?:i (?:don't like|hate|dislike|can't stand))\s+(.{3,40}?)(?:\.|,|!|\?|$)", "dislike"),
        # "My name is ..." patterns
        (r"(?:my name is|call me|i go by)\s+(\w+)", "name"),
        # "I work as/I'm a ..." professional patterns
        (r"(?:i work (?:as|in|at|for)|my job is)\s+(.{3,40}?)(?:\.|,|!|\?|$)", "profession"),
        # "I live in ..." patterns
        (r"(?:i live in|i'm from|i'm based in)\s+(.{3,40}?)(?:\.|,|!|\?|$)", "location"),
    ]

    # Topic extraction: important nouns/phrases to track
    _TOPIC_KEYWORDS = {
        'cooking', 'recipe', 'food', 'meal', 'garden', 'plant', 'weather',
        'health', 'medical', 'medicine', 'exercise', 'fitness', 'workout',
        'emergency', 'survival', 'preparedness', 'security', 'privacy',
        'network', 'smart home', 'automation', 'calendar', 'schedule',
        'task', 'reminder', 'music', 'movie', 'book', 'reading',
        'programming', 'code', 'python', 'science', 'history',
        'travel', 'camping', 'hiking', 'fishing', 'hunting',
        'radio', 'ham radio', 'electronics', 'repair', 'diy',
        'family', 'kids', 'pets', 'dog', 'cat', 'chicken',
        'solar', 'energy', 'water', 'farming', 'homestead',
    }

    MAX_FACTS = 30
    MAX_RECENT_TOPICS = 5

    def __init__(self, user_id: str, memory_dir: str | None = None):
        self.user_id = user_id

        if memory_dir is None:
            # Default: repo_root/user_memory/
            repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))))
            memory_dir = os.path.join(repo_root, "user_memory")

        self.memory_dir = memory_dir
        os.makedirs(memory_dir, exist_ok=True)
        self._file = os.path.join(memory_dir, f"{user_id}.json")

        # Load or initialize
        self.data = self._load()

    def _load(self) -> dict:
        """Load memory from disk or create empty structure."""
        if os.path.exists(self._file):
            try:
                with open(self._file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            'user_id': self.user_id,
            'topics': {},          # topic -> count
            'facts': [],           # list of {fact, category, timestamp}
            'preferences': {},     # key -> value
            'recent_topics': [],   # last N conversation summaries
            'last_updated': None,
        }

    def save(self):
        """Persist memory to disk."""
        self.data['last_updated'] = datetime.now().isoformat()
        with open(self._file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def extract_and_store(self, user_message: str):
        """Extract facts and topics from a user message and store them.

        Call this after each user message. Lightweight — no LLM calls.
        """
        lower = user_message.lower().strip()
        if len(lower) < 5:
            return

        # Extract facts from patterns
        for pattern, category in self._FACT_PATTERNS:
            matches = re.findall(pattern, lower, re.IGNORECASE)
            for match in matches:
                fact_text = match.strip()
                if len(fact_text) < 3 or len(fact_text) > 80:
                    continue
                # Deduplicate
                existing = [f['fact'] for f in self.data['facts']]
                if fact_text not in existing:
                    self.data['facts'].append({
                        'fact': fact_text,
                        'category': category,
                        'timestamp': datetime.now().isoformat(),
                    })
                    # Cap facts list
                    if len(self.data['facts']) > self.MAX_FACTS:
                        self.data['facts'] = self.data['facts'][-self.MAX_FACTS:]

        # Extract topics
        words = set(lower.split())
        for topic in self._TOPIC_KEYWORDS:
            topic_words = set(topic.split())
            if topic_words.issubset(words) or topic in lower:
                count = self.data['topics'].get(topic, 0)
                self.data['topics'][topic] = count + 1

    def update_recent_topic(self, chat_title: str):
        """Track recent conversation topics for continuity."""
        if not chat_title or chat_title == "New Chat":
            return
        recent = self.data['recent_topics']
        # Don't duplicate
        if recent and recent[-1] == chat_title:
            return
        recent.append(chat_title)
        if len(recent) > self.MAX_RECENT_TOPICS:
            self.data['recent_topics'] = recent[-self.MAX_RECENT_TOPICS:]

    def get_memory_context(self) -> str:
        """Build a concise context string for LLM system prompt injection.

        Returns a short paragraph summarizing what we know about the user,
        suitable for appending to the persona prompt.
        """
        parts = []

        # Top facts (most recent, up to 8)
        facts = self.data.get('facts', [])
        if facts:
            recent_facts = facts[-8:]
            fact_lines = []
            for f in recent_facts:
                fact_lines.append(f"- {f['fact'].capitalize()}")
            parts.append("Things this user has shared:\n" + "\n".join(fact_lines))

        # Top topics (most discussed)
        topics = self.data.get('topics', {})
        if topics:
            sorted_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)
            top = [t[0] for t in sorted_topics[:6]]
            parts.append(f"Topics they frequently discuss: {', '.join(top)}")

        # Recent conversation topics
        recent = self.data.get('recent_topics', [])
        if recent:
            parts.append(f"Recent conversations were about: {', '.join(recent[-3:])}")

        # Explicit preferences
        prefs = self.data.get('preferences', {})
        if prefs:
            pref_lines = [f"- {k}: {v}" for k, v in prefs.items()]
            parts.append("Their preferences:\n" + "\n".join(pref_lines))

        if not parts:
            return ""

        return "\nUSER MEMORY (use this to personalize responses):\n" + "\n".join(parts)
