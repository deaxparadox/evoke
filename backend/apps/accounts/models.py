from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom user model. Created before the first migration so it can change later."""
