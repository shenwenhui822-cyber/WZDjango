# 账户简报独立权限；波动率指标页权限名称调整

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0001_portal_access_permissions"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="portalaccess",
            options={
                "default_permissions": (),
                "managed": False,
                "permissions": [
                    ("view_fund_nav", "可查看基金净值页"),
                    ("view_nav_bench_compare", "可查看alpha产品表现页"),
                    ("view_alpha_t0", "可查看T0表现页"),
                    ("view_option_metrics", "可查看波动率指标页"),
                    ("view_account_brief", "可查看账户简报页"),
                ],
            },
        ),
    ]
