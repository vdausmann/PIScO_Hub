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
						python-dotenv
						h5py
                    ]))
					tailwindcss
					sqlite
					sqlite-web
                ];
                buildInputs = with pkgs; [
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
