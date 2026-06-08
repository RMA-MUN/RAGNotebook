import os
from datetime import datetime
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.migrations.loader import MigrationLoader


class Command(BaseCommand):
    help = '从当前 migrations 生成完整的 init_db.sql 文件'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='sql/init_db.sql',
            help='输出文件路径（默认: sql/init_db.sql）',
        )

    def handle(self, *args, **options):
        output_path = options['output']
        db_name = settings.DATABASES['default']['NAME']

        # 收集所有迁移（含 Django 内置 app）
        loader = MigrationLoader(None, ignore_no_migrations=True)

        # loader.disk_migrations: {(app_label, name): Migration}
        all_migrations = sorted(loader.disk_migrations.keys())

        if not all_migrations:
            self.stderr.write(self.style.WARNING('未发现任何迁移文件'))
            return

        lines = [
            '-- ============================================================',
            f'-- DjangoUserService 数据库初始化脚本',
            f'-- 数据库: {db_name}',
            f'-- 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            '-- ============================================================',
            '',
            f'CREATE DATABASE IF NOT EXISTS `{db_name}`',
            f'    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;',
            f'',
            f'USE `{db_name}`;',
            '',
        ]

        # 对每个 migration 执行 sqlmigrate 收集 SQL
        for app_label, name in all_migrations:
            out = StringIO()
            try:
                call_command('sqlmigrate', app_label, name, stdout=out, no_color=True)
                sql = out.getvalue().strip()
            except Exception as e:
                self.stderr.write(
                    self.style.WARNING(f'sqlmigrate 失败 ({app_label}.{name}): {e}')
                )
                continue

            if not sql:
                continue

            lines.append(f'-- {app_label}.{name}')
            lines.append(sql)
            lines.append('')

        # 生成 django_migrations 记录
        migration_records = []
        for app_label, name in all_migrations:
            migration_records.append(f"    ('{app_label}', '{name}', NOW())")

        if migration_records:
            lines.append('-- 标记 migrations 为已执行')
            lines.append('INSERT INTO `django_migrations` (`app`, `name`, `applied`) VALUES')
            lines.append(',\n'.join(migration_records) + ';')
            lines.append('')

        # 写入文件
        output_path = os.path.join(settings.BASE_DIR, output_path)
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        content = '\n'.join(lines)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)

        self.stdout.write(self.style.SUCCESS(f'SQL 文件已生成: {output_path}'))
        self.stdout.write(f'共 {len(all_migrations)} 个 migration，写入 {len(lines)} 行')
