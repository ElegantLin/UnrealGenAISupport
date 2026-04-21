// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "GenAssetTransactionUtils.generated.h"

/**
 * Safe-mutation runtime primitives: takes a snapshot of an asset/package,
 * exposes an opaque token so a Python caller can later roll back if the
 * high-level apply step fails or verification is rejected.
 *
 * The methods are static so they appear to Python as
 * ``unreal.GenAssetTransactionUtils.method_name``.
 */
UCLASS()
class GENERATIVEAISUPPORTEDITOR_API UGenAssetTransactionUtils : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * Duplicate the asset package into a temp snapshot location and return an
	 * opaque token on success.  Returns an empty string on failure.
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Transactions")
	static FString DuplicateForPreview(const FString& AssetPath);

	/**
	 * Mark the begin of a transaction.  This is a lightweight wrapper around
	 * FScopedTransaction that also records the token/asset association so a
	 * later apply/rollback can locate the snapshot.
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Transactions")
	static FString BeginTransaction(const FString& AssetPath, const FString& Description);

	/**
	 * Apply an already-modified asset: save the package and return a JSON
	 * mutation report ({"saved":bool, "path":"..."}).  The snapshot token is
	 * retained so rollback is still available until ``DiscardSnapshot`` is
	 * called.
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Transactions")
	static FString ApplyTransaction(const FString& SnapshotToken, const FString& ChangesJson);

	/**
	 * Restore the asset at the token's recorded path from its snapshot.
	 * Returns true on success.
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Transactions")
	static bool RollbackToSnapshot(const FString& SnapshotToken);

	/**
	 * Perform lightweight verification (asset exists, compiles if Blueprint).
	 * Returns a JSON object with {"passed":bool, "checks":[...]}.
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Transactions")
	static FString VerifyAsset(const FString& AssetPath);

	/**
	 * Remove snapshot artefacts for a token (called after a successful verify).
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Transactions")
	static bool DiscardSnapshot(const FString& SnapshotToken);
};
