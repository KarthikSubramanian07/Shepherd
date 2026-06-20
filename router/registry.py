"""
Routine registry: keyword sets → routine_id + variable extraction patterns.
Router selection stays DETERMINISTIC — no ML in this hot path.
A wrong match here moves the real mouse.
"""

REGISTRY: dict[str, dict] = {
    "ROUTINE_FORM_FILL": {
        "keywords": ["fill", "form", "apply", "application", "job", "submit", "intake"],
        "description": "Fill a form with applicant details",
        "variable_patterns": {
            "APPLICANT_NAME":  r"for\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
            "APPLICANT_EMAIL": r"email[:\s]+(\S+@\S+\.\S+)",
        },
        "variable_defaults": {
            "APPLICANT_NAME":  "Alex Johnson",
            "APPLICANT_EMAIL": "alex@example.com",
            "APPLICANT_PHONE": "555-0100",
        },
    },
    "ROUTINE_BROWSER_SHOWPIECE": {
        "keywords": ["browser", "web", "search", "open", "website", "navigate", "lookup"],
        "description": "Open a browser and perform a live web action via Browserbase",
        "variable_patterns": {
            "SEARCH_QUERY": r"search\s+(?:for\s+)?(.+)",
        },
        "variable_defaults": {
            "SEARCH_QUERY": "AI agent safety",
        },
    },
    "ROUTINE_LOCKED_FALLBACK": {
        "keywords": ["locked", "safe", "fallback", "demo", "test"],
        "description": "Deterministic offline fallback routine",
        "variable_patterns": {},
        "variable_defaults": {
            "NOTE_TEXT": "Shepherd demo run",
        },
    },
}

# Minimum keyword-match confidence to accept a route (0.0–1.0)
CONFIDENCE_THRESHOLD = 0.3
