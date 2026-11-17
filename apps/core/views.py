from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Role, Utilisateur
from .serializers import (
    ChangePasswordSerializer,
    RoleSerializer,
    UtilisateurSerializer,
)
from .permissions import PermissionMatrice, UtilisateurActif, role_actif
from .roles import ADMINISTRATEUR
from .utils import generate_default_password

class RoleViewSet(viewsets.ModelViewSet):
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    ressource = "roles"
    permission_classes = [PermissionMatrice]


class UtilisateurViewSet(viewsets.ModelViewSet):
    queryset = Utilisateur.objects.all()
    serializer_class = UtilisateurSerializer
    ressource = "utilisateurs"
    permission_classes = [PermissionMatrice]

    def _refuser_si_compte_administrateur(self, cible=None, role_demande=None):
        """Seul l'Administrateur attribue ce rôle ou agit sur un compte Administrateur.

        La matrice ne connaît que l'action : ce contrôle porte sur le compte visé
        et le rôle demandé (la gérante gère les autres comptes).
        """
        if role_actif(self.request.user) == ADMINISTRATEUR:
            return
        # Un compte qui ouvre l'administration Django (is_staff / is_superuser,
        # ex. createsuperuser) est protégé comme un compte Administrateur.
        cible_admin = cible is not None and (
            (cible.role is not None and cible.role.libelle == ADMINISTRATEUR)
            or cible.is_superuser
            or cible.is_staff
        )
        demande_admin = role_demande is not None and role_demande.libelle == ADMINISTRATEUR
        if cible_admin or demande_admin:
            raise PermissionDenied(
                "Seul l'Administrateur peut attribuer ce rôle ou modifier un compte Administrateur."
            )

    def perform_create(self, serializer):
        self._refuser_si_compte_administrateur(
            role_demande=serializer.validated_data.get("role")
        )
        serializer.save()

    def perform_update(self, serializer):
        self._refuser_si_compte_administrateur(
            cible=serializer.instance,
            role_demande=serializer.validated_data.get("role"),
        )
        serializer.save()

    def perform_destroy(self, instance):
        self._refuser_si_compte_administrateur(cible=instance)
        instance.delete()

    @action(
        detail=False,
        methods=["post"],
        url_path="change-password",
    )
    def change_password(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={"request": request},
        )

        serializer.is_valid(raise_exception=True)

        user = request.user

        user.set_password(
            serializer.validated_data["nouveau_mot_de_passe"]
        )
        user.must_change_password = False

        user.save(
            update_fields=["password", "must_change_password"]
        )

        return Response({
            "detail": "Mot de passe modifié avec succès."
        })
    @action(
    detail=True,
    methods=["post"],
    url_path="reset-password",
    )
    def reset_password(self, request, pk=None):
        user = self.get_object()
        self._refuser_si_compte_administrateur(cible=user)

        password = generate_default_password()

        user.set_password(password)
        user.must_change_password = True

        user.save(
            update_fields=["password", "must_change_password"]
        )

        return Response({
            "detail": "Mot de passe réinitialisé avec succès.",
            "mot_de_passe_temporaire": password,
        })
        

@api_view(["GET"])
@permission_classes([UtilisateurActif])
def me(request):
	return Response(
		UtilisateurSerializer(request.user).data
	)

@api_view(["POST"])
@permission_classes([UtilisateurActif])
def logout(request):
	refresh_token = request.data.get("refresh")

	if not refresh_token:
		return Response(
			{"detail": "Refresh token requis."},
			status=400,
		)

	try:
		token = RefreshToken(refresh_token)
		token.blacklist()

		return Response(
			{"detail": "Déconnexion réussie."},
			status=200,
		)

	except Exception:
		return Response(
			{"detail": "Refresh token invalide."},
			status=400,
		)