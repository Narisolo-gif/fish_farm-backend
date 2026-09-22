from rest_framework import serializers

from .models import Role, Utilisateur
from .utils import generate_default_password


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = [
            "id",
            "libelle",
            "description",
        ]


class UtilisateurSerializer(serializers.ModelSerializer):
    role = RoleSerializer(read_only=True)
    role_id = serializers.PrimaryKeyRelatedField(
        source="role",
        queryset=Role.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )

    mot_de_passe_temporaire = serializers.CharField(
        read_only=True
    )

    class Meta:
        model = Utilisateur
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "role_id",
            "statut",
            "is_active",
            "must_change_password",
            "mot_de_passe_temporaire",
        ]
        read_only_fields = [
            "id",
            "is_active",
            "must_change_password",
            "mot_de_passe_temporaire",
        ]

    def create(self, validated_data):
        password = generate_default_password()

        user = Utilisateur(**validated_data)
        user.set_password(password)
        user.must_change_password = True
        user.save()

        user.mot_de_passe_temporaire = password

        return user
    
    def update(self, instance, validated_data):
        statut = validated_data.get("statut")

        if statut == Utilisateur.Statut.INACTIF:
            instance.is_active = False

        elif statut == Utilisateur.Statut.ACTIF:
            instance.is_active = True

        return super().update(instance, validated_data)
    
class ChangePasswordSerializer(serializers.Serializer):
    ancien_mot_de_passe = serializers.CharField(
        write_only=True
    )
    nouveau_mot_de_passe = serializers.CharField(
        write_only=True,
        min_length=8
    )

    def validate(self, attrs):
        user = self.context["request"].user

        if not user.check_password(attrs["ancien_mot_de_passe"]):
            raise serializers.ValidationError({
                "ancien_mot_de_passe": "Ancien mot de passe incorrect."
            })

        if attrs["ancien_mot_de_passe"] == attrs["nouveau_mot_de_passe"]:
            raise serializers.ValidationError({
                "nouveau_mot_de_passe": "Le nouveau mot de passe doit être différent de l'ancien."
            })

        return attrs
class ResetPasswordSerializer(serializers.Serializer):
    mot_de_passe_temporaire = serializers.CharField(read_only=True)