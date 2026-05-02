// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "GenEditorSessionUtils.generated.h"

/**
 * Capture / restore of the "current editor state" for the P1.5 restart flow.
 * Methods are static so Python sees them as
 * ``unreal.GenEditorSessionUtils.capture_session_json`` etc.
 */
UCLASS()
class GENERATIVEAISUPPORTEDITOR_API UGenEditorSessionUtils : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Session")
	static FString CaptureSessionJson();

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Session")
	static FString SaveSessionJson(const FString& SessionJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Session")
	static FString LoadLastSessionJson();

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Session")
	static FString OpenAssetForRestore(const FString& AssetPath, bool bIsPrimary);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Session")
	static FString BringAssetToFront(const FString& AssetPath);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Session")
	static FString FocusGraph(const FString& AssetPath, const FString& GraphPath);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Session")
	static FString FocusNode(const FString& AssetPath, const FString& GraphPath, const FString& NodeGuid);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Session")
	static FString CaptureActiveViewportPng(const FString& OutputPath, int32 Width = 0, int32 Height = 0);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Session")
	static FString SelectActor(const FString& ActorLabel);

private:
	static FString GetSessionFilePath();
};
