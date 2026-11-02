from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView

from .roles import is_support, is_support_or_admin


class AuthenticationProbe(APIView):
    def get(self, request):
        return Response({'user_id': request.user.pk})


# Test-only URL configuration; no probe endpoint is added to production URLs.
urlpatterns = [path('_test/auth/', AuthenticationProbe.as_view())]


class RoleTests(TestCase):
    def test_roles_require_active_users_and_staff_is_not_a_support_role(self):
        User = get_user_model()
        support_group = Group.objects.get(name='Support')
        employee = User.objects.create_user(username='employee')
        staff_employee = User.objects.create_user(username='staff', is_staff=True)
        support = User.objects.create_user(username='support')
        support.groups.add(support_group)
        administrator = User.objects.create_user(username='administrator', is_superuser=True)
        inactive_support = User.objects.create_user(username='inactive_support', is_active=False)
        inactive_support.groups.add(support_group)
        inactive_admin = User.objects.create_user(
            username='inactive_admin', is_superuser=True, is_active=False,
        )
        cases = [
            (employee, False, False),
            (staff_employee, False, False),
            (support, True, True),
            (administrator, False, True),
            (inactive_support, False, False),
            (inactive_admin, False, False),
            (AnonymousUser(), False, False),
        ]
        for user, support_member, privileged in cases:
            with self.subTest(user=user.username):
                self.assertEqual(is_support(user), support_member)
                self.assertEqual(is_support_or_admin(user), privileged)


@override_settings(ROOT_URLCONF=__name__)
class TokenAuthenticationTests(TestCase):
    def test_global_authentication_with_a_command_provisioned_token(self):
        user = get_user_model().objects.create_user(username='employee')
        call_command('drf_create_token', user.username, stdout=StringIO())
        token = Token.objects.get(user=user)
        client = APIClient()

        response = client.get('/_test/auth/')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['WWW-Authenticate'], 'Token')
        client.credentials(HTTP_AUTHORIZATION='Token invalid-token')
        self.assertEqual(client.get('/_test/auth/').status_code, 401)
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        response = client.get('/_test/auth/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'user_id': user.pk})

        user.is_active = False
        user.save(update_fields=['is_active'])
        self.assertEqual(client.get('/_test/auth/').status_code, 401)
