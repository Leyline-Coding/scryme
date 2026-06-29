# Homebrew cask for the scryme desktop app (macOS, arm64).
# Update `version` and `sha256` on each release; get the sum from the published .dmg.
cask "scryme" do
  version "0.10.0"
  sha256 "REPLACE_WITH_arm64_dmg_SHA256"

  url "https://github.com/Leyline-Coding/scryme/releases/download/v#{version}/scryme-#{version}-arm64.dmg"
  name "scryme"
  desc "Self-hostable, Scryfall-like search for your MTG collection"
  homepage "https://github.com/Leyline-Coding/scryme"

  depends_on arch: :arm64

  app "scryme.app"

  zap trash: [
    "~/Library/Application Support/scryme",
  ]
end
