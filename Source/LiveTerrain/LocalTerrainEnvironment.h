#pragma once

class UWorld;

namespace LocalTerrain
{
    /** Native offline daylight environment shared by every prepared geographic zone. */
    void CreateEnvironment(UWorld* World);
}
