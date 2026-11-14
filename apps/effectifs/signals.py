from django.dispatch import Signal


sortie_stock_validee = Signal()
sortie_stock_avant_annulation = Signal()
# Émis avant la création d'un contre-mouvement ; un récepteur peut refuser
# en levant une ValidationError (ex. mouvement d'un lot de grossissement clôturé).
mouvement_avant_annulation = Signal()
