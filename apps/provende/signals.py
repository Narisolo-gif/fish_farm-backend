from django.dispatch import Signal


# `provende` n'importe pas les modules de production : il les notifie.
# Un récepteur peut refuser l'opération en levant une ValidationError
# (ex. chiffres figés d'un lot de grossissement clôturé).

# Émis avant l'enregistrement d'une distribution, le bassin étant verrouillé.
# Arguments : bassin, date_locale (date de la distribution, fuseau du projet).
distribution_avant_enregistrement = Signal()

# Émis avant l'annulation d'une distribution valide.
# Arguments : distribution.
distribution_avant_annulation = Signal()
