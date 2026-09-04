"""
Nyrqis OS - Language Learning App
Flashcards with spaced repetition, progress tracking, and multi-language support.
"""

import time
import random
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class SpacedRepetitionLevel(Enum):
    NEW = 0
    LEARNING = 1
    YOUNG = 2
    MATURE = 3


@dataclass
class Flashcard:
    front: str
    back: str
    language_from: str = "en"
    language_to: str = "es"
    level: SpacedRepetitionLevel = SpacedRepetitionLevel.NEW
    ease_factor: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    last_review: float = 0.0
    next_review: float = 0.0
    tags: List[str] = field(default_factory=list)
    total_reviews: int = 0
    correct_reviews: int = 0
    audio_hint: str = ""
    example_sentence: str = ""

    @property
    def accuracy(self) -> float:
        if self.total_reviews == 0:
            return 0.0
        return self.correct_reviews / self.total_reviews

    @property
    def is_due(self) -> bool:
        return time.time() >= self.next_review

    @property
    def level_icon(self) -> str:
        icons = {
            SpacedRepetitionLevel.NEW: "🆕",
            SpacedRepetitionLevel.LEARNING: "📖",
            SpacedRepetitionLevel.YOUNG: "🌱",
            SpacedRepetitionLevel.MATURE: "🌳",
        }
        return icons.get(self.level, "?")


@dataclass
class Deck:
    name: str
    language_from: str = "en"
    language_to: str = "es"
    cards: List[Flashcard] = field(default_factory=list)
    created_at: float = 0.0
    daily_new_limit: int = 20
    daily_review_limit: int = 100

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    @property
    def total_cards(self) -> int:
        return len(self.cards)

    @property
    def new_cards(self) -> int:
        return sum(1 for c in self.cards if c.level == SpacedRepetitionLevel.NEW)

    @property
    def learning_cards(self) -> int:
        return sum(1 for c in self.cards if c.level == SpacedRepetitionLevel.LEARNING)

    @property
    def young_cards(self) -> int:
        return sum(1 for c in self.cards if c.level == SpacedRepetitionLevel.YOUNG)

    @property
    def mature_cards(self) -> int:
        return sum(1 for c in self.cards if c.level == SpacedRepetitionLevel.MATURE)

    @property
    def due_cards(self) -> List[Flashcard]:
        return [c for c in self.cards if c.is_due]

    @property
    def retention_rate(self) -> float:
        reviewed = [c for c in self.cards if c.total_reviews > 0]
        if not reviewed:
            return 0.0
        return sum(c.accuracy for c in reviewed) / len(reviewed)


@dataclass
class StudySession:
    deck_name: str
    cards_studied: int = 0
    correct: int = 0
    incorrect: int = 0
    start_time: float = 0.0
    end_time: float = 0.0
    time_spent_s: float = 0.0

    def __post_init__(self):
        if self.start_time == 0.0:
            self.start_time = time.time()

    @property
    def accuracy(self) -> float:
        total = self.correct + self.incorrect
        if total == 0:
            return 0.0
        return self.correct / total

    @property
    def cards_per_minute(self) -> float:
        if self.time_spent_s == 0:
            return 0.0
        return (self.cards_studied / self.time_spent_s) * 60


class LanguageLearningApp:
    def __init__(self):
        self.decks: List[Deck] = []
        self.current_deck: Optional[Deck] = None
        self.current_card: Optional[Flashcard] = None
        self.sessions: List[StudySession] = []
        self.current_session: Optional[StudySession] = None
        self.showing_answer: bool = False
        self.streak_days: int = 0
        self.total_words_learned: int = 0
        self.languages: List[str] = ["es", "fr", "de", "ja", "ko", "zh", "pt", "it"]
        self._create_sample_decks()

    def _create_sample_decks(self):
        spanish_verbs = [
            ("ser", "to be (permanent)", "Yo soy estudiante.", "I am a student."),
            ("estar", "to be (temporary)", "Estoy cansado.", "I am tired."),
            ("tener", "to have", "Tengo hambre.", "I am hungry."),
            ("hacer", "to do/make", "Hago la tarea.", "I do the homework."),
            ("ir", "to go", "Voy a la escuela.", "I go to school."),
            ("poder", "to be able to", "Puedo hablar.", "I can speak."),
            ("decir", "to say", "Digo la verdad.", "I say the truth."),
            ("dar", "to give", "Doy un regalo.", "I give a gift."),
            ("saber", "to know (fact)", "Sé la respuesta.", "I know the answer."),
            ("querer", "to want", "Quiero agua.", "I want water."),
            ("haber", "to have (aux)", "He comido.", "I have eaten."),
            ("llegar", "to arrive", "Llego a tiempo.", "I arrive on time."),
            ("poner", "to put", "Pongo la mesa.", "I set the table."),
            ("salir", "to leave", "Salgo de casa.", "I leave home."),
            ("venir", "to come", "Vengo de España.", "I come from Spain."),
        ]
        spanish_cards = [
            Flashcard(front=f, back=b, language_from="en", language_to="es",
                       example_sentence=s, tags=["verbs", "common"])
            for f, b, s, _ in spanish_verbs
        ]
        for i, card in enumerate(spanish_cards):
            card.level = [SpacedRepetitionLevel.MATURE, SpacedRepetitionLevel.YOUNG,
                          SpacedRepetitionLevel.LEARNING, SpacedRepetitionLevel.NEW][i % 4]
            card.total_reviews = random.randint(0, 20)
            card.correct_reviews = int(card.total_reviews * random.uniform(0.6, 1.0))

        spanish_nouns = [
            ("casa", "house", "La casa es grande.", "The house is big."),
            ("perro", "dog", "El perro corre.", "The dog runs."),
            ("libro", "book", "Leo un libro.", "I read a book."),
            ("agua", "water", "Bebo agua.", "I drink water."),
            ("tiempo", "time/weather", "No tengo tiempo.", "I don't have time."),
        ]
        spanish_cards.extend([
            Flashcard(front=f, back=b, language_from="en", language_to="es",
                       example_sentence=s, tags=["nouns"])
            for f, b, s, _ in spanish_nouns
        ])

        french_cards = [
            Flashcard(front="bonjour", back="hello", language_from="en", language_to="fr",
                       example_sentence="Bonjour, comment allez-vous?", tags=["greetings"]),
            Flashcard(front="merci", back="thank you", language_from="en", language_to="fr",
                       example_sentence="Merci beaucoup!", tags=["common"]),
            Flashcard(front="s'il vous plaît", back="please", language_from="en", language_to="fr",
                       example_sentence="Un café, s'il vous plaît.", tags=["common"]),
            Flashcard(front="au revoir", back="goodbye", language_from="en", language_to="fr",
                       example_sentence="Au revoir et bonne journée!", tags=["greetings"]),
            Flashcard(front="oui", back="yes", language_from="en", language_to="fr",
                       example_sentence="Oui, je comprends.", tags=["common"]),
        ]

        japanese_cards = [
            Flashcard(front="こんにちは", back="hello", language_from="en", language_to="ja",
                       example_sentence="こんにちは、元気ですか？", tags=["greetings"]),
            Flashcard(front="ありがとう", back="thank you", language_from="en", language_to="ja",
                       example_sentence="ありがとうございます！", tags=["common"]),
            Flashcard(front="はい", back="yes", language_from="en", language_to="ja",
                       example_sentence="はい、そうです。", tags=["common"]),
            Flashcard(front="いいえ", back="no", language_from="en", language_to="ja",
                       example_sentence="いいえ、違います。", tags=["common"]),
            Flashcard(front="すみません", back="excuse me/sorry", language_from="en", language_to="ja",
                       example_sentence="すみません、道を教えてください。", tags=["common"]),
        ]

        self.decks = [
            Deck(name="Spanish Verbs", language_from="en", language_to="es",
                 cards=[c for c in spanish_cards if "verbs" in c.tags]),
            Deck(name="Spanish Nouns", language_from="en", language_to="es",
                 cards=[c for c in spanish_cards if "nouns" in c.tags]),
            Deck(name="French Basics", language_from="en", language_to="fr",
                 cards=french_cards),
            Deck(name="Japanese Basics", language_from="en", language_to="ja",
                 cards=japanese_cards),
        ]
        self.current_deck = self.decks[0]

    def get_all_due_cards(self) -> List[Flashcard]:
        due = []
        for deck in self.decks:
            due.extend(deck.due_cards)
        return due

    def start_session(self, deck_name: str) -> Optional[StudySession]:
        deck = next((d for d in self.decks if d.name == deck_name), None)
        if not deck:
            return None
        self.current_session = StudySession(deck_name=deck_name)
        self.current_deck = deck
        return self.current_session

    def next_card(self) -> Optional[Flashcard]:
        if not self.current_deck:
            return None
        due = self.current_deck.due_cards
        if due:
            self.current_card = due[0]
        elif self.current_deck.cards:
            self.current_card = random.choice(self.current_deck.cards)
        else:
            return None
        self.showing_answer = False
        return self.current_card

    def show_answer(self) -> str:
        if not self.current_card:
            return ""
        self.showing_answer = True
        return self.current_card.back

    def grade_card(self, quality: int) -> bool:
        if not self.current_card or not self.current_session:
            return False
        card = self.current_card
        card.total_reviews += 1
        card.last_review = time.time()
        self.current_session.cards_studied += 1

        if quality >= 3:
            card.correct_reviews += 1
            self.current_session.correct += 1
            if card.repetitions == 0:
                card.interval_days = 1
            elif card.repetitions == 1:
                card.interval_days = 6
            else:
                card.interval_days = int(card.interval_days * card.ease_factor)
            card.repetitions += 1
        else:
            self.current_session.incorrect += 1
            card.repetitions = 0
            card.interval_days = 0

        card.ease_factor = max(1.3, card.ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        card.next_review = time.time() + card.interval_days * 86400

        if card.repetitions >= 5:
            card.level = SpacedRepetitionLevel.MATURE
        elif card.repetitions >= 2:
            card.level = SpacedRepetitionLevel.YOUNG
        elif card.total_reviews > 0:
            card.level = SpacedRepetitionLevel.LEARNING

        self.total_words_learned = sum(
            1 for d in self.decks for c in d.cards
            if c.level in (SpacedRepetitionLevel.YOUNG, SpacedRepetitionLevel.MATURE)
        )
        return True

    def end_session(self) -> Optional[StudySession]:
        if self.current_session:
            self.current_session.end_time = time.time()
            self.current_session.time_spent_s = (
                self.current_session.end_time - self.current_session.start_time
            )
            self.sessions.append(self.current_session)
            session = self.current_session
            self.current_session = None
            return session
        return None

    def get_language_stats(self) -> Dict[str, Dict[str, int]]:
        stats: Dict[str, Dict[str, int]] = {}
        for deck in self.decks:
            lang = deck.language_to
            if lang not in stats:
                stats[lang] = {"total": 0, "new": 0, "learning": 0, "young": 0, "mature": 0}
            stats[lang]["total"] += deck.total_cards
            stats[lang]["new"] += deck.new_cards
            stats[lang]["learning"] += deck.learning_cards
            stats[lang]["young"] += deck.young_cards
            stats[lang]["mature"] += deck.mature_cards
        return stats

    def search_cards(self, query: str) -> List[Tuple[Flashcard, str]]:
        results = []
        q = query.lower()
        for deck in self.decks:
            for card in deck.cards:
                if q in card.front.lower() or q in card.back.lower():
                    results.append((card, deck.name))
        return results

    def add_card(self, deck_name: str, front: str, back: str, **kwargs) -> bool:
        deck = next((d for d in self.decks if d.name == deck_name), None)
        if not deck:
            return False
        card = Flashcard(front=front, back=back, **kwargs)
        deck.cards.append(card)
        return True

    def get_study_stats(self) -> Dict:
        total_cards = sum(d.total_cards for d in self.decks)
        total_due = len(self.get_all_due_cards())
        avg_retention = (
            sum(d.retention_rate for d in self.decks) / len(self.decks)
            if self.decks else 0.0
        )
        total_reviews = sum(
            c.total_reviews for d in self.decks for c in d.cards
        )
        return {
            "total_cards": total_cards,
            "total_due": total_due,
            "avg_retention": round(avg_retention, 3),
            "total_reviews": total_reviews,
            "words_learned": self.total_words_learned,
            "sessions": len(self.sessions),
            "streak_days": self.streak_days,
        }
