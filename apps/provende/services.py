from django.db import transaction

from .models import Provende, TypeProvende


@transaction.atomic
def creer_type_provende(validated_data):
    """Crée un type de provende dans une transaction."""

    return TypeProvende.objects.create(**validated_data)


@transaction.atomic
def creer_provende(validated_data):
    """Crée une provende dans une transaction."""

    return Provende.objects.create(**validated_data)