# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2020-10-15 19:20
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contentcuration', '0126_auto_20201004_2037'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='Answers',
            new_name='Answer',
        ),
    ]
