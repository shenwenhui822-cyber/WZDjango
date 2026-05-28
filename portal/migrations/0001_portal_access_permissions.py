# 门户页面级权限（PortalAccess 不占业务表）

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PortalAccess",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
            ],
            options={
                "permissions": [
                    ("view_fund_nav", "可查看基金净值页"),
                    ("view_nav_bench_compare", "可查看alpha产品表现页"),
                    ("view_alpha_t0", "可查看T0表现页"),
                    ("view_option_metrics", "可查看股指期货页"),
                ],
                "managed": False,
                "default_permissions": (),
            },
        ),
    ]
