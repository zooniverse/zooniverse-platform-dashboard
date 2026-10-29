"""Approved project-launch counts from the existing local JSON snapshot."""

from dash import register_page

from zooniverse_dashboard.projects_ui import projects_layout, register_projects_callback

register_page(__name__, path="/projects", name="Projects", order=4)


def layout(**_kwargs):
    return projects_layout()


register_projects_callback()
