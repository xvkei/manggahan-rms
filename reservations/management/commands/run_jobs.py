"""
Run the automatic rules once:
  * release unpaid bookings after their payment deadline
  * text payment-deadline and game reminders
  * mark finished games as completed

For a real deployment, schedule this every 5 minutes (cron, Task Scheduler,
or a hosting provider's scheduler):
    */5 * * * *  cd /path/to/project && python manage.py run_jobs
"""
from django.core.management.base import BaseCommand

from reservations.services import run_housekeeping


class Command(BaseCommand):
    help = "Run auto-release, reminders, and completion checks once."

    def handle(self, *args, **options):
        result = run_housekeeping(force=True)
        self.stdout.write(self.style.SUCCESS(
            "Done. Released: {released}, game reminders: {reminded}, "
            "deadline reminders: {deadline_warned}, completed: {completed}".format(**result)
        ))
