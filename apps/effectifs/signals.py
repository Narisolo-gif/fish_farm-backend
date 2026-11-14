from django.dispatch import Signal


# Émis avant l'annulation d'une sortie de stock validée ; un récepteur peut
# refuser en levant une ValidationError (ex. lot de grossissement déjà pesé).
sortie_stock_avant_annulation = Signal()
# Émis avant la création d'un contre-mouvement ; un récepteur peut refuser
# en levant une ValidationError (ex. mouvement d'un lot de grossissement clôturé).
mouvement_avant_annulation = Signal()
