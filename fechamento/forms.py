"""forms.py — Formulários da área de gestão."""

from django import forms
from django.contrib.auth.models import User

from .models import Empresa, Equipe, Perfil


def nome_papel(user):
    """Rótulo de exibição de um usuário: 'Nome (Papel)'."""
    nome = user.first_name or user.username
    papel = getattr(getattr(user, "perfil", None), "papel", "operador")
    return f"{nome} ({dict(Perfil.Papel.choices).get(papel, papel)})"


class EquipeForm(forms.ModelForm):
    class Meta:
        model = Equipe
        fields = ["nome"]
        widgets = {"nome": forms.TextInput(attrs={"class": "form-control", "autofocus": True})}
        labels = {"nome": "Nome da equipe"}


class EmpresaForm(forms.ModelForm):
    class Meta:
        model = Empresa
        fields = ["razao_social", "cnpj", "equipe", "ativa"]
        widgets = {
            "razao_social": forms.TextInput(attrs={"class": "form-control"}),
            "cnpj": forms.TextInput(attrs={"class": "form-control", "placeholder": "00.000.000/0000-00"}),
            "equipe": forms.Select(attrs={"class": "form-select"}),
            "ativa": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {"razao_social": "Razão social", "cnpj": "CNPJ", "equipe": "Equipe padrão", "ativa": "Empresa ativa"}

    def clean_cnpj(self):
        cnpj = (self.cleaned_data.get("cnpj") or "").strip()
        if cnpj:
            qs = Empresa.objects.filter(cnpj=cnpj)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("Já existe uma empresa com esse CNPJ.")
        return cnpj


class UsuarioCriarForm(forms.Form):
    nome = forms.CharField(label="Nome completo", max_length=150)
    username = forms.CharField(label="Usuário (login)", max_length=150)
    email = forms.EmailField(label="E-mail", required=False)
    papel = forms.ChoiceField(label="Papel", choices=Perfil.Papel.choices)
    equipes = forms.ModelMultipleChoiceField(
        label="Equipes que atende", queryset=Equipe.objects.all(), required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        help_text="Operador vê só as empresas dessas equipes. Gestor vê todas.",
    )
    senha = forms.CharField(label="Senha", widget=forms.PasswordInput, min_length=6)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Já existe um usuário com esse login.")
        return username

    def save(self):
        d = self.cleaned_data
        user = User.objects.create_user(
            username=d["username"], email=d["email"], password=d["senha"], first_name=d["nome"],
        )
        perfil = Perfil.objects.create(usuario=user, papel=d["papel"])
        perfil.equipes.set(d["equipes"])
        return user


class UsuarioEditarForm(forms.Form):
    nome = forms.CharField(label="Nome completo", max_length=150)
    email = forms.EmailField(label="E-mail", required=False)
    papel = forms.ChoiceField(label="Papel", choices=Perfil.Papel.choices)
    equipes = forms.ModelMultipleChoiceField(
        label="Equipes que atende", queryset=Equipe.objects.all(), required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        help_text="Operador vê só as empresas dessas equipes. Gestor vê todas.",
    )
    ativo = forms.BooleanField(label="Usuário ativo", required=False)
    nova_senha = forms.CharField(
        label="Nova senha (deixe em branco para manter)", widget=forms.PasswordInput,
        required=False, min_length=6,
    )

    def __init__(self, *args, usuario=None, **kwargs):
        self._usuario = usuario
        super().__init__(*args, **kwargs)

    def save(self):
        user = self._usuario
        d = self.cleaned_data
        user.first_name = d["nome"]
        user.email = d["email"]
        user.is_active = d["ativo"]
        if d["nova_senha"]:
            user.set_password(d["nova_senha"])
        user.save()
        perfil, _ = Perfil.objects.get_or_create(usuario=user)
        perfil.papel = d["papel"]
        perfil.save()
        perfil.equipes.set(d["equipes"])
        return user
