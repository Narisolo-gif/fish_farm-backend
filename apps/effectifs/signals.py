from django.dispatch import Signal


# Émis avant l'annulation d'une sortie de stock validée ; un récepteur peut
# refuser en levant une ValidationError (ex. lot de grossissement déjà pesé).
sortie_stock_avant_annulation = Signal()
# Émis avant la création d'un contre-mouvement ; un récepteur peut refuser
# en levant une ValidationError (ex. mouvement d'un lot de grossissement clôturé).
mouvement_avant_annulation = Signal()
# Émis avant la création d'un mouvement (hors annulation), une fois le bassin
# verrouillé ; un récepteur peut refuser en levant une ValidationError (ex.
# registre figé d'un lot de grossissement clôturé). `operation_source_*`
# identifie l'opération qui écrit le mouvement, ou vaut None.
mouvement_avant_enregistrement = Signal()
