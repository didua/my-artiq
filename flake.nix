{
  description = "Minimal dev shell for my-artiq";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          git
          python3
          python3Packages.pip
        ];

        shellHook = ''
          echo "Entered my-artiq dev shell"
          python3 --version
        '';
      };
    };
}
