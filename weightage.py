"""Official TNPSC AE (Civil) Code 398 syllabus weightage, and helpers
to compute a star-rating for how important a unit/topic is.
"""

UNIT_WEIGHTAGE = {
    "Unit 1: Building Materials and Construction Practices": 20,
    "Unit 2: Engineering Survey": 15,
    "Unit 3: Engineering Mechanics and Strength of Materials": 20,
    "Unit 4: Structural Analysis": 20,
    "Unit 5: Geotechnical Engineering": 25,
    "Unit 6: Environmental Engineering and Pollution Control": 15,
    "Unit 7: Design of RCC, PSC and Steel Structures": 30,
    "Unit 8: Hydraulics and Water Resources Engineering": 20,
    "Unit 9: Urban and Transportation Engineering": 20,
    "Unit 10: Project Management and Estimation": 15,
}

TOTAL_QUESTIONS = sum(UNIT_WEIGHTAGE.values())  # 220


def stars_for_unit(unit_name: str) -> str:
    """Return a star rating string based on official question count for the unit."""
    count = UNIT_WEIGHTAGE.get(unit_name, 15)
    if count >= 30:
        return "⭐⭐⭐⭐"
    if count >= 25:
        return "⭐⭐⭐"
    if count >= 20:
        return "⭐⭐"
    return "⭐"


def unit_share(unit_name: str) -> float:
    """Fraction of a full-syllabus exam this unit should occupy."""
    return UNIT_WEIGHTAGE.get(unit_name, 15) / TOTAL_QUESTIONS
