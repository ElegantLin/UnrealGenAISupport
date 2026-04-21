// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "GenAnimationBlueprintUtils.generated.h"

/**
 * Read + semantic-write helpers for UAnimBlueprint (P4 + P5).
 *
 * All methods return JSON strings so they can be driven identically from
 * the socket server regardless of which language authored them. Each write
 * is expected to:
 *   1. Locate or create the target sub-graph (state machine / state /
 *      transition / alias / cached-pose).
 *   2. Mutate the graph (``Modify`` + ``NotifyGraphChanged``).
 *   3. Call ``FKismetEditorUtilities::CompileBlueprint`` to keep the editor
 *      session consistent.
 *   4. Save the package via ``UEditorAssetLibrary::SaveLoadedAsset``.
 *
 * Write results include ``compiled``/``saved`` booleans plus a best-effort
 * ``verification`` block that Python code uses to build mutation reports.
 */
UCLASS()
class GENERATIVEAISUPPORTEDITOR_API UGenAnimationBlueprintUtils : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// --- P4 Read ---

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString GetAnimBlueprintStructure(const FString& AnimBlueprintPath);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString GetGraphNodes(const FString& AnimBlueprintPath, const FString& GraphPath);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString GetGraphPins(
		const FString& AnimBlueprintPath,
		const FString& GraphPath,
		const FString& NodeId);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString ResolveGraphByPath(const FString& AnimBlueprintPath, const FString& GraphPath);

	// --- P5 Write ---

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString CreateStateMachine(const FString& AnimBlueprintPath, const FString& PayloadJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString CreateState(const FString& AnimBlueprintPath, const FString& PayloadJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString CreateTransition(const FString& AnimBlueprintPath, const FString& PayloadJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString SetTransitionRule(const FString& AnimBlueprintPath, const FString& PayloadJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString CreateStateAlias(const FString& AnimBlueprintPath, const FString& PayloadJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString SetAliasTargets(const FString& AnimBlueprintPath, const FString& PayloadJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString SetStateSequenceAsset(const FString& AnimBlueprintPath, const FString& PayloadJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString SetStateBlendSpaceAsset(const FString& AnimBlueprintPath, const FString& PayloadJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString SetCachedPoseNode(const FString& AnimBlueprintPath, const FString& PayloadJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString SetDefaultSlotChain(const FString& AnimBlueprintPath, const FString& PayloadJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation Blueprint")
	static FString SetApplyAdditiveChain(const FString& AnimBlueprintPath, const FString& PayloadJson);
};
