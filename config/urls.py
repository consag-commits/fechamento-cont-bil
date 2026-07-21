"""URL configuration for config project."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from fechamento import sso

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Entrada vinda do Portal de Sistemas (ticket assinado, chega por POST).
    path("entrar-pelo-portal/", sso.entrar_pelo_portal, name="entrar_pelo_portal"),
    path("", include("fechamento.urls")),
]
