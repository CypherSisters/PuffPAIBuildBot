"""
PuffPAI - Privacy and User-Focused Fortified Personal AI

A local-first personal AI assistant that runs entirely in your home:
local inference, encrypted private data, curated knowledge, and smart-home
integration, with no cloud dependency. Nothing leaves your house without
your consent.

License: Apache-2.0
"""

__version__ = "0.1.0-alpha"
__license__ = "Apache-2.0"

#: The one name every outside request from this build carries. Several free
#: services (MusicBrainz, Nominatim, the US weather service, Wikipedia, Open
#: Food Facts, Crossref, Open Library) ask for a descriptive one; it names the
#: product and where to read about it, and nothing about the household.
USER_AGENT = (f"PuffPAI-BuildBot/{__version__} "
              f"(+https://github.com/CypherSisters/PuffPAIBuildBot)")
