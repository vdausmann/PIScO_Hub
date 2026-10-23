import importlib
from pathlib import Path
from .models import db, Viewer


class ViewerManager:


    def __init__(self, app, folder):

        self.app = app
        self.folder = Path(folder)



    def load_viewers(self):

        for viewer in self.folder.iterdir():

            config_file = (
                viewer /
                "config.json"
            )


            if not config_file.exists():
                print("No config file found for viewer", viewer.name)
                continue


            with open(config_file) as f:
                import json
                config = json.load(f)


            module_name = (
                "viewers" 
                + "."
                + viewer.name
                + "."
                + config["module"]
            )



            # One viewer with a missing dependency must not take the Hub
            # down with it; it is left out and says why in the log.
            try:
                module = importlib.import_module(
                    module_name
                )

                blueprint = getattr(
                    module,
                    config["blueprint"]
                )
            except Exception as e:
                print(f"Viewer {viewer.name} not loaded: {e!r}")
                continue


            self.app.register_blueprint(
                blueprint
            )


            existing_tool = Viewer.query.filter_by(name=config["name"]).first()
            if existing_tool:
                continue


            viewer_object = Viewer()
            viewer_object.name = config["name"]
            viewer_object.extensions = json.dumps(config["extensions"])
            viewer_object.description = config["description"]
            viewer_object.path = viewer.name
            viewer_object.type = config["type"]
            viewer_object.url = "/viewer/" + config["name"].lower().replace("_", "-").replace(" ", "-")


            db.session.add(viewer_object)

        db.session.commit()

