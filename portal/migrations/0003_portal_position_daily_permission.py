# 日度持仓分析页权限

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0002_portal_account_brief_permission"),
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
                    ("view_position_daily", "可查看日度持仓分析页"),
                ],
            },
        ),
    ]
