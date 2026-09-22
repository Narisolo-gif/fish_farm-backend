from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import Role, Utilisateur
from .serializers import (
    ChangePasswordSerializer,
    RoleSerializer,
    UtilisateurSerializer,
)
from .permissions import IsAdministrateur
from .utils import generate_default_password

class RoleViewSet(viewsets.ModelViewSet):
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [IsAdministrateur]


class UtilisateurViewSet(viewsets.ModelViewSet):
    queryset = Utilisateur.objects.all()
    serializer_class = UtilisateurSerializer
    permission_classes = [IsAdministrateur]

    @action(
        detail=False,
        methods=["post"],
        url_path="change-password",
        permission_classes=[IsAuthenticated],
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
    permission_classes=[IsAdministrateur],
    )
    def reset_password(self, request, pk=None):
        user = self.get_object()

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