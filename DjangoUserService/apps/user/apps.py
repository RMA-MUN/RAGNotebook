import logging
import os
import sys
import threading

from django.apps import AppConfig
from django.db import connection, DatabaseError, OperationalError

logger = logging.getLogger(__name__)


class UserConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.user'

    def ready(self):
        """启动时自动检测并执行数据库迁移"""
        # 仅在 dev server 主进程中执行（避免热加载重复运行）
        if os.environ.get('RUN_MAIN') != 'true':
            return
        # 避免在管理命令中递归执行
        if 'manage.py' in sys.argv and any(
            cmd in sys.argv for cmd in ('makemigrations', 'migrate', 'sqlmigrate', 'showmigrations')
        ):
            return

        # 延迟执行，避免在 app 初始化期间访问数据库
        thread = threading.Thread(target=self._auto_migrate, daemon=True)
        thread.start()

    def _ensure_database_exists(self) -> bool:
        """数据库不存在时自动创建"""
        from django.conf import settings

        db_settings = settings.DATABASES['default']
        db_name = db_settings['NAME']

        # 先尝试正常连接
        try:
            connection.ensure_connection()
            return True
        except (DatabaseError, OperationalError) as e:
            err = str(e).lower()
            if 'unknown database' not in err and 'does not exist' not in err:
                raise  # 非"数据库不存在"的错误，向上抛
        except Exception:
            raise

        # 连接 MySQL 但不指定数据库，执行 CREATE DATABASE
        try:
            import pymysql
            conn = pymysql.connect(
                host=db_settings['HOST'],
                port=int(db_settings['PORT']),
                user=db_settings['USER'],
                password=db_settings['PASSWORD'],
                charset='utf8mb4',
            )
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                    f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            conn.commit()
            conn.close()
            # 重置 Django 连接（连接指向了不存在的数据库，需重建）
            connection.close()
            logger.info('数据库 `%s` 已自动创建', db_name)
            return True
        except Exception as e:
            logger.error('自动创建数据库失败: %s', e)
            return False

    def _auto_migrate(self):
        from django.core.management import call_command, CommandError

        # 1. 确保数据库存在（不存在则自动创建）
        if not self._ensure_database_exists():
            logger.warning('数据库不可用，跳过自动迁移')
            return

        # 2. 检查业务表是否已存在
        try:
            existing_tables = connection.introspection.table_names()
        except Exception:
            existing_tables = []

        from django.apps import apps
        app_tables = []
        for app_config in apps.get_app_configs():
            if not app_config.name.startswith('apps.'):
                continue
            for model in app_config.get_models():
                app_tables.append(model._meta.db_table)

        # 3. 自动迁移
        if not (app_tables and all(t in existing_tables for t in app_tables)):
            try:
                logger.info('生成迁移文件...')
                call_command('makemigrations', interactive=False)
                logger.info('执行数据库迁移...')
                call_command('migrate', interactive=False)
                logger.info('数据库自动迁移完成')
            except (CommandError, Exception) as e:
                logger.error('自动迁移失败: %s', e)
                return

        # 4. 确保测试用户存在
        self._ensure_test_user()

    def _ensure_test_user(self):
        """确保测试用户存在（admin/admin1234）"""
        try:
            from django.contrib.auth.hashers import make_password
            from .models import User, UserStatusChoice

            if not User.objects.filter(username='admin').exists():
                User.objects.create(
                    username='admin',
                    email='admin@example.com',
                    password=make_password('admin1234'),
                    status=UserStatusChoice.ACTIVE,
                    is_active=True,
                )
                logger.info('测试用户 admin 已创建')
        except Exception as e:
            logger.warning('创建测试用户失败: %s', e)
