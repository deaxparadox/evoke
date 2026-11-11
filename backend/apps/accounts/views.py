from django.contrib.auth import authenticate, login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


def _user_json(user):
    return {"id": user.pk, "username": user.get_username()}


@method_decorator(csrf_protect, name="dispatch")  # DRF only checks CSRF for logged-in users; a login form needs it too
class LoginView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = authenticate(request, **data.validated_data)
        if user is None:
            return Response({"detail": "Wrong username or password."}, status=status.HTTP_401_UNAUTHORIZED)
        login(request, user)
        return Response(_user_json(user))


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(ensure_csrf_cookie, name="dispatch")  # a client calls this first and gets the CSRF cookie it needs to log in
class MeView(APIView):
    def get(self, request):
        return Response(_user_json(request.user))
