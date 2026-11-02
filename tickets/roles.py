SUPPORT_GROUP_NAME = 'Support'


def is_support(user):
    return (
        user.is_authenticated
        and user.is_active
        and user.groups.filter(name=SUPPORT_GROUP_NAME).exists()
    )


def is_support_or_admin(user):
    return (
        user.is_authenticated
        and user.is_active
        and (user.is_superuser or is_support(user))
    )
