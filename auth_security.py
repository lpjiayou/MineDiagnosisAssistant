import re

import bcrypt


# ============================================================
# 认证安全参数
# ============================================================

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32

PASSWORD_MIN_LENGTH = 8

# bcrypt当前只接受不超过72字节的密码。
PASSWORD_MAX_BYTES = 72

BCRYPT_ROUNDS = 12


USERNAME_PATTERN = re.compile(
    r"^[a-zA-Z0-9_.-]+$"
)


class AuthValidationError(ValueError):
    """
    用户名或密码格式不符合要求。
    """


def normalize_username(
    username: str,
) -> str:
    """
    统一用户名格式。

    用户名不区分英文字母大小写。
    """

    return username.strip().lower()


def validate_username(
    username: str,
) -> str:
    """
    验证并返回规范化后的用户名。

    允许：
    - 英文字母
    - 数字
    - 下划线
    - 英文句点
    - 英文减号
    """

    normalized_username = normalize_username(
        username
    )

    if not normalized_username:
        raise AuthValidationError(
            "用户名不能为空。"
        )

    if not (
        USERNAME_MIN_LENGTH
        <= len(normalized_username)
        <= USERNAME_MAX_LENGTH
    ):
        raise AuthValidationError(
            "用户名长度必须为"
            f"{USERNAME_MIN_LENGTH}～"
            f"{USERNAME_MAX_LENGTH}个字符。"
        )

    if not USERNAME_PATTERN.fullmatch(
        normalized_username
    ):
        raise AuthValidationError(
            "用户名只能包含英文字母、数字、"
            "下划线、英文句点和英文减号。"
        )

    return normalized_username


def validate_password(
    password: str,
) -> str:
    """
    验证密码复杂度。

    当前要求：
    - 至少8个字符
    - 至少包含一个英文字母
    - 至少包含一个数字
    - UTF-8编码后不能超过72字节
    """

    if not password:
        raise AuthValidationError(
            "密码不能为空。"
        )

    if len(password) < PASSWORD_MIN_LENGTH:
        raise AuthValidationError(
            f"密码至少需要{PASSWORD_MIN_LENGTH}个字符。"
        )

    if not re.search(
        r"[A-Za-z]",
        password,
    ):
        raise AuthValidationError(
            "密码至少需要包含一个英文字母。"
        )

    if not re.search(
        r"\d",
        password,
    ):
        raise AuthValidationError(
            "密码至少需要包含一个数字。"
        )

    password_bytes = password.encode(
        "utf-8"
    )

    if len(password_bytes) > PASSWORD_MAX_BYTES:
        raise AuthValidationError(
            "密码UTF-8编码后不能超过72字节。"
        )

    return password


def hash_password(
    password: str,
) -> str:
    """
    生成bcrypt密码哈希。

    返回值可以直接保存到数据库。
    """

    validated_password = validate_password(
        password
    )

    password_bytes = validated_password.encode(
        "utf-8"
    )

    salt = bcrypt.gensalt(
        rounds=BCRYPT_ROUNDS
    )

    password_hash = bcrypt.hashpw(
        password_bytes,
        salt,
    )

    return password_hash.decode(
        "utf-8"
    )


def verify_password(
    password: str,
    password_hash: str,
) -> bool:
    """
    验证用户输入的密码是否匹配数据库哈希。
    """

    if not password or not password_hash:
        return False

    try:
        password_bytes = password.encode(
            "utf-8"
        )

        if len(password_bytes) > PASSWORD_MAX_BYTES:
            return False

        return bcrypt.checkpw(
            password_bytes,
            password_hash.encode("utf-8"),
        )

    except (
        ValueError,
        TypeError,
        UnicodeError,
    ):
        return False