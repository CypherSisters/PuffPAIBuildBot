"""
Offline knowledge-library descriptions.

The static catalog of offline knowledge libraries and their one-line
descriptions, which the API server uses to describe what's available offline.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

# Human-readable descriptions for the bundled offline knowledge libraries.
# Keyed by library display name (see indexed_libraries.json / the knowledge
# registry). Used by the API server to list what's available offline.
LIBRARY_DESCRIPTIONS: dict[str, str] = {
    "RationalWiki": "Critical encyclopedia covering pseudoscience, authoritarianism, and logical fallacies",
    "Best of Wikipedia": "Curated collection of Wikipedia's highest-quality featured articles",
    "Wikispecies": "Comprehensive taxonomy directory of all living species",
    "Ready.gov": "US government emergency preparedness guides and disaster planning",
    "Explain XKCD": "Detailed explanations of XKCD webcomics covering science, math, and tech",
    "TED surveillance": "TED Talks exploring surveillance, privacy, and government monitoring",
    "Wikipedia": "Full offline English Wikipedia encyclopedia",
    "Amateur Radio Q&A": "Stack Exchange Q&A for ham radio operators and enthusiasts",
    "Canadian prepper": "Cold-climate emergency preparedness and winter survival guides",
    "TED military": "TED Talks on military topics, veterans, and defense policy",
    "Wikisource": "Library of free public-domain source texts and historical documents",
    "Water Treatment Library": "Comprehensive guides on water purification, filtration, and treatment methods",
    "Physics by Wikipedia": "Wikipedia articles covering physics concepts and discoveries",
    "TED solar energy": "TED Talks on solar power, renewable energy, and sustainability",
    "WikEM": "Emergency medicine reference with clinical decision support",
    "OpenStreetMap Wiki": "Documentation for the open-source mapping project",
    "Homebrewing Q&A": "Stack Exchange Q&A for home beer and wine brewing",
    "TED justice system": "TED Talks on criminal justice, reform, and the legal system",
    "TED human rights": "TED Talks on human rights, equality, and social justice",
    "MDWiki Medical Encyclopedia": "Offline medical encyclopedia for clinical reference",
    "TED democracy": "TED Talks on democracy, governance, and civic participation",
    "MediaWiki": "Documentation for the MediaWiki software platform",
    "Wikipedia in simple English": "Wikipedia written in simplified English for accessibility",
    "Urban Prepper": "Urban survival and emergency preparedness for city environments",
    "Biology Courses": "University-level biology course materials and textbooks",
    "Medical Library": "General medical reference library for health information",
    "Wikivoyage": "Free worldwide travel guide with destination information",
    "WikiSummaries": "Concise summaries of Wikipedia's most important articles",
    "History by Wikipedia": "Wikipedia articles covering historical events and periods",
    "Gardening & Landscaping Q&A": "Stack Exchange Q&A for gardening and landscaping",
    "Information Security Q&A": "Stack Exchange Q&A for cybersecurity and infosec",
    "Engineering Q&A": "Stack Exchange Q&A for engineering topics",
    "Medical Sciences Q&A": "Stack Exchange Q&A for medical science questions",
    "TED plants": "TED Talks on botany, agriculture, and plant science",
    "Medicine LibreTexts": "Open-access medical textbooks and educational materials",
    "Home Improvement Q&A": "Stack Exchange Q&A for DIY and home repair",
    "TED law": "TED Talks on law, legal systems, and regulation",
    "Military Medicine": "Field medicine, combat casualty care, and military health references",
    "A Library of Knots": "Illustrated guide to knot-tying techniques and applications",
    "TED water": "TED Talks on water access, conservation, and purification",
    "TED maps": "TED Talks on cartography, geography, and spatial data",
    "TED sociology": "TED Talks on sociology, culture, and human behavior",
    "TED coronavirus": "TED Talks on COVID-19, pandemics, and public health response",
    "Knots by Wikipedia": "Wikipedia articles on knot types and their uses",
}

__all__ = ["LIBRARY_DESCRIPTIONS"]
