#include "TerrainHUD.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Misc/App.h"

void ATerrainHUD::DrawHUD()
{
    Super::DrawHUD();
    if (!Canvas || !GEngine) return;
    const double Delta = FApp::GetDeltaTime();
    if (Delta <= 0. || !FMath::IsFinite(Delta)) return;
    const double Blend = 1. - FMath::Exp(-Delta / .5);
    SmoothedFrameSeconds = SmoothedFrameSeconds > 0.
        ? FMath::Lerp(SmoothedFrameSeconds, Delta, Blend) : Delta;
    const double FPS = 1. / SmoothedFrameSeconds;
    const FLinearColor Colour = FPS >= 55. ? FLinearColor(.3f,1.f,.4f)
        : (FPS >= 30. ? FLinearColor(1.f,.85f,.2f) : FLinearColor(1.f,.3f,.25f));
    const float X = FMath::Max(20.f, Canvas->ClipX - 200.f);
    DrawRect(FLinearColor(0.f,0.f,0.f,.55f), X - 8.f, 14.f, 190.f, 34.f);
    DrawText(FString::Printf(TEXT("%.0f FPS | %.1f ms"), FPS, SmoothedFrameSeconds * 1000.),
        Colour, X, 22.f, GEngine->GetSmallFont(), 1.2f);
}
