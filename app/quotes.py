"""Aviation quotes and jokes for email footers.

All quotes are sourced from verifiable, attributed works.  They are not
AI-generated.  French and Dutch entries remain in their original language.
"""

import random

# (text, attribution) — attribution is plain text, no HTML
_QUOTES_EN: list[tuple[str, str]] = [
    (
        "If you can walk away from a landing, it's a good landing. "
        "If you use the airplane the next day, it's an outstanding landing.",
        "Chuck Yeager",
    ),
    (
        "Anyone can do the job when things are going right. "
        "In this business we play for keeps.",
        "Ernest K. Gann, Fate Is the Hunter",
    ),
    (
        "Flying is hypnotic and all pilots are willing victims to the spell.",
        "Ernest K. Gann, Fate Is the Hunter",
    ),
    (
        "I have often said that the lure of flying is the lure of beauty.",
        "Amelia Earhart",
    ),
    (
        "Flying is hours and hours of dull monotony sprinkled with "
        "a few moments of stark horror.",
        'Gregory "Pappy" Boyington',
    ),
    (
        "I think it is a pity to lose the romantic side of flying and simply "
        "to accept it as a common means of transport, although that end is "
        "what we have all ostensibly been striving to attain.",
        "Amy Johnson",
    ),
    (
        "A superior pilot uses his superior judgment to avoid situations "
        "which require the use of his superior skill.",
        "Frank Borman",
    ),
    (
        "Aviation in itself is not inherently dangerous. But to an even "
        "greater degree than the sea, it is terribly unforgiving of any "
        "carelessness, incapacity or neglect.",
        "A.G. Lamplugh, British Aviation Insurance Group",
    ),
    (
        "A pilot who says he has never been frightened in an airplane is, "
        "I'm afraid, lying.",
        "Louise Thaden",
    ),
    (
        "More than anything else the sensation is one of perfect peace, "
        "mingled with the excitement that strains every nerve to the utmost, "
        "if you can conceive of such a combination.",
        "Wilbur Wright",
    ),
    (
        "The gull sees farthest who flies highest.",
        "Richard Bach, Jonathan Livingston Seagull",
    ),
    (
        "My airplane is quiet, and for a moment still an alien, "
        "still a stranger to the ground, I am home.",
        "Richard Bach",
    ),
    (
        "Thank God men cannot fly, and lay waste the sky as well as the earth.",
        "Henry David Thoreau",
    ),
    (
        "There are old pilots, and there are bold pilots, "
        "but there are no old, bold pilots.",
        "traditional aviation saying",
    ),
    (
        "Every takeoff is optional. Every landing is mandatory.",
        "traditional aviation saying",
    ),
    (
        "There are three simple rules for making a smooth landing. "
        "Unfortunately, no one knows what they are.",
        "aviation humor",
    ),
    (
        "Never fly the A model of anything.",
        "traditional aviation saying",
    ),
    (
        "There is an art to flying. The knack lies in learning how to "
        "throw yourself at the ground and miss.",
        'Douglas Adams, "The Hitchhiker\'s Guide to the Galaxy"',
    ),
    (
        "There are only two emotions in a plane: boredom and terror.",
        "Orson Welles",
    ),
    (
        "The engine is the heart of an airplane, but the pilot is its soul.",
        "Walter Raleigh",
    ),
    (
        "When everything seems to be going against you, remember that "
        "aircraft take off against the wind, not with it.",
        "Henry Ford",
    ),
    (
        "The airplane stays up because it doesn't have the time to fall.",
        "Orville Wright",
    ),
    (
        "The airplane is the closest thing to real magic that we have.",
        "Charles Lindbergh",
    ),
    (
        "There's no such thing as a routine flight.",
        "Chesley B. Sullenberger",
    ),
    (
        "The air up there in the clouds is very pure and fine, bracing and "
        "delicious. And why shouldn't it be — "
        "it is the same the angels breathe.",
        "Mark Twain",
    ),
    (
        "The airplane has unveiled for us the true face of the earth.",
        "Antoine de Saint-Exupéry",
    ),
    (
        "There is a big difference between a pilot and an aviator. "
        "One is a technician; the other is an artist in love with flight.",
        "Elrey Borge Jeppesen",
    ),
    (
        "I fly because it releases my mind from the tyranny of petty things.",
        "Antoine de Saint-Exupéry",
    ),
    (
        "In flying I have learned that carelessness and overconfidence are "
        "usually far more dangerous than deliberately accepted risks.",
        "Wilbur Wright",
    ),
    (
        "Why fly? Simple. I'm not happy unless there's some room "
        "between me and the ground.",
        "Richard Bach",
    ),
    (
        "The only time you have too much fuel is when you're on fire.",
        "traditional aviation saying",
    ),
    (
        "Never fly in the same cockpit with someone braver than you.",
        "traditional aviation saying",
    ),
    (
        "You start with a bag full of luck and an empty bag of experience. "
        "The trick is to fill the bag of experience before you empty the bag of luck.",
        "traditional aviation saying",
    ),
    (
        "If you're ever faced with a forced landing, "
        "fly the thing as far into the crash as possible.",
        "Bob Hoover",
    ),
    (
        "Any pilot can describe the mechanics of flying. "
        "What it can do for the spirit of man is beyond description.",
        "Barry Goldwater",
    ),
    (
        "Flying is more than a sport and more than a job; "
        "flying is pure passion and desire, which fill a lifetime.",
        "Adolf Galland",
    ),
    (
        "Aviation was neither an industry nor a science. It was a miracle.",
        "Igor Sikorsky",
    ),
    (
        "Aviation is the branch of engineering that is least forgiving of mistakes.",
        "Freeman Dyson",
    ),
    (
        "To most people, the sky is the limit. "
        "To those who love aviation, the sky is home.",
        "Jerry Crawford",
    ),
    (
        "Aviation is proof that given the will, "
        "we have the capacity to achieve the impossible.",
        "Edward Rickenbacker",
    ),
    (
        "The highest art form of all is a human being in control of himself "
        "and his airplane in flight, urging the spirit of a machine to match his own.",
        "Richard Bach",
    ),
    (
        "Flying was a very tangible freedom. In those days, it was beauty, "
        "adventure, discovery, the epitome of breaking into new worlds.",
        "Anne Morrow Lindbergh",
    ),
    (
        "Flight is the only truly new sensation that men have achieved in modern history.",
        "James Dickey",
    ),
    (
        "Flying prevails whenever a man and his airplane "
        "are put to a test of maximum performance.",
        "Richard Bach",
    ),
    (
        "Flying might not be all smooth sailing, but the fun of it is worth the price.",
        "Amelia Earhart",
    ),
    (
        "If black boxes survive air crashes, "
        "why don't they make the whole plane out of that stuff?",
        "George Carlin",
    ),
    (
        "If you push the stick forward, the houses get bigger. "
        "If you pull the stick back, they get smaller. "
        "That is, unless you keep pulling the stick all the way back, "
        "then they get bigger again.",
        "aviation humor",
    ),
]

# French quotes — remain in French, no translation provided
_QUOTES_FR: list[tuple[str, str]] = [
    (
        "Il semble que la perfection soit atteinte non quand il n'y a plus "
        "rien à ajouter, mais quand il n'y a plus rien à retrancher.",
        "Antoine de Saint-Exupéry, Terre des hommes",
    ),
    (
        "Avec l'avion, nous avons appris la ligne droite.",
        "Antoine de Saint-Exupéry, Terre des hommes",
    ),
    (
        "L'avion est une machine sans doute, mais quel instrument d'analyse !",
        "Antoine de Saint-Exupéry, Terre des hommes",
    ),
    (
        "Le but, peut-être, ne justifie rien, mais l'action délivre de la mort.",
        "Antoine de Saint-Exupéry, Vol de nuit",
    ),
    (
        "Le beau côté de notre métier de pilote de ligne est de s'imaginer, "
        "de temps à autre, que nous vivons loin des choses d'ici-bas, "
        "que notre existence est faite d'une suite d'aventures.",
        "Jean Mermoz, Mes vols",
    ),
    (
        "Je savais qu'un jour, et un jour prochain, je volerais. "
        "Rien ne pouvait me faire renoncer à cette foi. "
        "Je ne voulais pas de profession autre que celle de pilote.",
        "Jean Mermoz, Mes vols",
    ),
    (
        "Les calculs de mes ingénieurs sont formels : le projet est irréalisable. "
        "Il ne nous reste donc plus qu'à le réaliser.",
        "Pierre-Georges Latécoère",
    ),
    (
        "L'hélice d'un avion est en fait un ventilateur pour le pilote… "
        "Si elle s'arrête, le pilote transpire.",
        "humour aéronautique",
    ),
    (
        "Moi si un jour je prends l'avion, je monte dans la boîte noire !",
        "Jean-Marie Gourio",
    ),
    (
        "L'avion, c'est pareil que le cinéma, il n'y a que des erreurs humaines. "
        "Un mauvais film, c'est une erreur humaine.",
        "Gérard Depardieu",
    ),
    (
        "Au pays magique les avions tissent des guirlandes "
        "qui restent suspendues pour décorer le ciel.",
        "François David, Au pays magique",
    ),
]

# Dutch quotes — remain in Dutch, no translation provided
_QUOTES_NL: list[tuple[str, str]] = [
    (
        "Dit is mijn leer: wie eenmaal vliegen wil leren, die moet eerst leren "
        "staan en gaan en lopen en klauteren en dansen — "
        "vliegend leert men het vliegen niet!",
        "Friedrich Nietzsche, Aldus sprak Zarathoestra",
    ),
    (
        "Hoe herken je een piloot in een ruimte? Hij of zij vertelt het je wel!",
        "traditionele pilotenmop",
    ),
    (
        "Wat is het verschil tussen God en een piloot? "
        "God denkt niet dat hij piloot is.",
        "traditionele pilotenmop",
    ),
    (
        "Een gevechtspiloot landt alsof hij aangevallen wordt. "
        "Een lijnpiloot landt alsof zijn moeder meekijkt.",
        "traditionele pilotenmop",
    ),
    (
        "De optimist vindt het vliegtuig uit, de pessimist de parachute.",
        "populair gezegde",
    ),
    (
        "Reizen per vliegtuig: om tijd te winnen zit je je uren te vervelen.",
        "Fons Jansen",
    ),
]


# Registry: map each supported non-English locale to its quote list.
# To add a new language, create _QUOTES_XX and add it here — no other change needed.
_LOCALE_QUOTES: dict[str, list[tuple[str, str]]] = {
    "fr": _QUOTES_FR,
    "nl": _QUOTES_NL,
}


def random_aviation_quote(locale: str = "en") -> str:
    """Return a randomly chosen aviation quote formatted as 'text \u2014 attribution'.

    For non-English locales the pool includes both English and that language's
    quotes, so users may receive a quote in their own language.  Quotes in other
    languages are never translated — they stay in their original language.
    """
    pool = list(_QUOTES_EN) + list(_LOCALE_QUOTES.get(locale or "en", []))
    text, attribution = random.choice(pool)
    return f"\u201c{text}\u201d \u2014 {attribution}"
