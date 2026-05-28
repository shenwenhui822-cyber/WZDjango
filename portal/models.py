"""门户权限占位模型（业务数据在 MongoDB，此处仅声明页面级权限）。"""

from django.db import models


class PortalAccess(models.Model):
    """
    不建业务表，仅用于在 auth 中注册自定义权限。
    migrate 后可在 Admin「用户/组」中分配。
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("view_fund_nav", "可查看基金净值页"),
            ("view_nav_bench_compare", "可查看alpha产品表现页"),
            ("view_alpha_t0", "可查看T0表现页"),
            ("view_option_metrics", "可查看股指期货页"),
        ]
