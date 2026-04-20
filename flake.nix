{
inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    flake-utils = {
        url = "github:numtide/flake-utils";
        inputs.system.follows = "systems";
    };
};

outputs = { self, nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem
    (system:
    let
        pkgs = import nixpkgs {
            inherit system;
            config.allowUnfree = true;
        };
        libs = [
            pkgs.libcxx
            pkgs.eigen
        ];


        # # Ensures all child processes die when you Ctrl+C
        # trap 'kill 0' EXIT
        #
        # # Get the absolute path of the project root
        #
        # echo "[*] Initializing Tailwind CSS..."
        # # Run it once WITHOUT watch first to ensure it actually builds
        # ${pkgs.nodePackages.tailwindcss}/bin/tailwindcss \
        #   -i "$PROJECT_ROOT/app/static/css/input.css" \
        #   -o "$PROJECT_ROOT/app/static/css/main.css"
        #
        # echo "[*] Starting Tailwind Watcher in background..."
        # ${pkgs.nodePackages.tailwindcss}/bin/tailwindcss \
        #   -i "$PROJECT_ROOT/app/static/css/input.css" \
        #   -o "$PROJECT_ROOT/app/static/css/main.css" --watch &
		run_server = pkgs.writeShellScriptBin "run_server" ''
			PROJECT_ROOT=$(pwd)
			echo "[*] Starting Flask server..."
			${pkgs.python3}/bin/python -u "$PROJECT_ROOT/main.py"
		'';

    in
        with pkgs;
            {
            devShells.default = mkShell {
                nativeBuildInputs = with pkgs; [
                    bashInteractive
                    pkg-config
                    (pkgs.python312.withPackages(ps: with ps;[
                        numpy
						#(opencv4.override {enableGtk3 = true;})
						werkzeug
						pandas
						flask
						flask-sqlalchemy
						flask-login
						flask-sock
						python-dotenv
						h5py
						requests
						gunicorn
						gevent
                    ]))
					tailwindcss
					sqlite
					sqlite-web
					nodePackages.tailwindcss
                ];
                buildInputs = with pkgs; [
					run_server
                ];
                shellHook = ''
					echo "PIScOHub"
					export FLASK_APP=app.py
					export FLASK_ENV=development
                '';
                LD_LIBRARY_PATH = "${pkgs.lib.makeLibraryPath libs}";
            };
        }
    );
}
