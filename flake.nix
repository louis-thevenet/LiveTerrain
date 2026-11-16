{
  description = "LiveTerrain — acquisition de données IGN et OSM";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  outputs = { self, nixpkgs }: let
    systems = [ "x86_64-linux" "aarch64-linux" ];
    eachSystem = f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));
  in {
    packages = eachSystem (pkgs: {
      ue-shell = pkgs.buildFHSEnv {
        name = "liveterrain-ue-shell";
        targetPkgs = p: with p; [
          bash git git-lfs cmake ninja gnumake nasm pkg-config curl zip unzip python3 cacert
          perl autoconf automake libtool bison flex gperf which util-linux
          gcc glibc zlib openssl icu libxml2 ncurses5 libuuid expat
          vulkan-loader mesa libgbm libGL libGLU libxkbcommon fontconfig freetype
          libx11 libxext libxrender libxi libxcursor
          libxrandr libxinerama libxcb libxscrnsaver
          libxfixes libxcomposite libxdamage
          alsa-lib libpulseaudio glib gtk3 SDL2 udev dbus nss nspr cups libdrm
          at-spi2-atk at-spi2-core libxslt cairo pango gdk-pixbuf atk
        ];
        runScript = pkgs.writeShellScript "ue-command" ''
          export SDL_VIDEODRIVER="''${UE_SDL_VIDEODRIVER:-x11}"
          export LD_LIBRARY_PATH="''${UNREAL_ENGINE_ROOT}/Engine/Binaries/Linux''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
          exec "$@"
        '';
      };
    });
    devShells = eachSystem (pkgs: {
      default = pkgs.mkShell {
        packages = [
          (pkgs.python3.withPackages (p: [ p.requests p.numpy p.pyproj p.rasterio p.shapely ]))
          pkgs.gdal pkgs.proj pkgs.cacert
          pkgs.git pkgs.git-lfs pkgs.cmake pkgs.ninja pkgs.gnumake pkgs.nasm pkgs.pkg-config pkgs.curl pkgs.zip pkgs.unzip
        ];
        GDAL_PAM_ENABLED = "NO";
        PYTHONDONTWRITEBYTECODE = "1";
        shellHook = ''echo "LiveTerrain: python tools/geodata/download.py --help"'';
      };
    });
  };
}
